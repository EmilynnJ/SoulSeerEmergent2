import os
import asyncpg
import stripe
import json
import asyncio
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from decimal import Decimal
from fastapi import FastAPI, HTTPException, Depends, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from dotenv import load_dotenv
import jwt
import requests
from contextlib import asynccontextmanager
import websockets
import uuid
import logging

# Import WebRTC signaling
from webrtc_signaling import signaling_server, get_rtc_configuration

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables
DATABASE_URL = os.getenv("DATABASE_URL")
CLERK_SECRET_KEY = os.getenv("CLERK_SECRET_KEY")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SIGNING_SECRET")

# Initialize Stripe
stripe.api_key = STRIPE_SECRET_KEY

# Database connection pool
db_pool = None

# WebSocket connections for real-time features
websocket_connections: Dict[str, WebSocket] = {}

# Session billing tracking
active_sessions: Dict[str, dict] = {}

# Pydantic models
class User(BaseModel):
    id: str
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    role: str = "client"  # client, reader, admin
    created_at: datetime
    updated_at: datetime

class Reader(BaseModel):
    id: str
    user_id: str
    bio: Optional[str] = None
    specialties: List[str] = []
    is_online: bool = False
    chat_rate_per_minute: float = 0.0
    phone_rate_per_minute: float = 0.0
    video_rate_per_minute: float = 0.0
    availability_status: str = "offline"  # offline, online, busy
    created_at: datetime
    updated_at: datetime

class Client(BaseModel):
    id: str
    user_id: str
    balance: float = 0.0
    created_at: datetime
    updated_at: datetime

class ReadingSession(BaseModel):
    id: str
    client_id: str
    reader_id: str
    session_type: str  # chat, phone, video
    status: str  # pending, active, completed, cancelled
    rate_per_minute: float
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    total_minutes: float = 0.0
    total_amount: float = 0.0
    room_id: str
    created_at: datetime
    updated_at: datetime

class ReaderStatus(BaseModel):
    availability_status: str
    chat_rate_per_minute: Optional[float] = None
    phone_rate_per_minute: Optional[float] = None
    video_rate_per_minute: Optional[float] = None

class SessionRequest(BaseModel):
    reader_id: str
    session_type: str  # chat, phone, video

class SessionAction(BaseModel):
    session_id: str
    action: str  # accept, reject, start, end

class AddFundsRequest(BaseModel):
    amount: float  # Amount in dollars

class WebRTCMessage(BaseModel):
    type: str
    target: Optional[str] = None
    data: Optional[dict] = None

# Database initialization
async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    
    # Create tables if they don't exist
    async with db_pool.acquire() as conn:
        # Users table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id VARCHAR PRIMARY KEY,
                email VARCHAR UNIQUE NOT NULL,
                first_name VARCHAR,
                last_name VARCHAR,
                role VARCHAR DEFAULT 'client',
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        ''')
        
        # Readers table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS readers (
                id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::text,
                user_id VARCHAR REFERENCES users(id) ON DELETE CASCADE,
                bio TEXT,
                specialties JSONB DEFAULT '[]',
                is_online BOOLEAN DEFAULT FALSE,
                chat_rate_per_minute DECIMAL(10,2) DEFAULT 0.00,
                phone_rate_per_minute DECIMAL(10,2) DEFAULT 0.00,
                video_rate_per_minute DECIMAL(10,2) DEFAULT 0.00,
                availability_status VARCHAR DEFAULT 'offline',
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        ''')
        
        # Clients table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS clients (
                id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::text,
                user_id VARCHAR REFERENCES users(id) ON DELETE CASCADE,
                balance DECIMAL(10,2) DEFAULT 0.00,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        ''')
        
        # Reading sessions table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS reading_sessions (
                id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::text,
                client_id VARCHAR REFERENCES clients(id),
                reader_id VARCHAR REFERENCES readers(id),
                session_type VARCHAR NOT NULL,
                status VARCHAR DEFAULT 'pending',
                rate_per_minute DECIMAL(10,2),
                start_time TIMESTAMP,
                end_time TIMESTAMP,
                total_minutes DECIMAL(10,2) DEFAULT 0.00,
                total_amount DECIMAL(10,2) DEFAULT 0.00,
                room_id VARCHAR UNIQUE,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        ''')

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    yield
    # Shutdown
    if db_pool:
        await db_pool.close()

# FastAPI app
app = FastAPI(lifespan=lifespan)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security
security = HTTPBearer()

# Clerk authentication
async def verify_clerk_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    token = credentials.credentials
    
    try:
        # Verify JWT token with Clerk
        # In production, you should verify the signature using Clerk's JWKS
        decoded_token = jwt.decode(token, options={"verify_signature": False})
        user_id = decoded_token.get("sub")
        
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        
        return {"user_id": user_id, "token_data": decoded_token}
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token")

# Database helper functions
async def get_user_by_id(user_id: str) -> Optional[dict]:
    async with db_pool.acquire() as conn:
        result = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
        return dict(result) if result else None

async def get_or_create_user(user_data: dict) -> dict:
    user_id = user_data["user_id"]
    token_data = user_data["token_data"]
    
    async with db_pool.acquire() as conn:
        # Check if user exists
        existing_user = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
        
        if existing_user:
            return dict(existing_user)
        
        # Create new user
        email = token_data.get("email", "")
        first_name = token_data.get("first_name", "")
        last_name = token_data.get("last_name", "")
        
        await conn.execute(
            """INSERT INTO users (id, email, first_name, last_name, role) 
               VALUES ($1, $2, $3, $4, 'client')""",
            user_id, email, first_name, last_name
        )
        
        # Create client profile
        await conn.execute(
            "INSERT INTO clients (user_id) VALUES ($1)",
            user_id
        )
        
        return await get_user_by_id(user_id)

# API Routes

@app.get("/api/status")
async def get_status():
    return {"status": "ok", "service": "SoulSeer API"}

@app.get("/api/health")
async def health_check():
    try:
        async with db_pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        raise HTTPException(status_code=503, detail="Database connection failed")

@app.get("/api/user/profile")
async def get_user_profile(user_data: dict = Depends(verify_clerk_token)):
    user = await get_or_create_user(user_data)
    return user

@app.get("/api/readers/available")
async def get_available_readers():
    """Get all currently available readers"""
    async with db_pool.acquire() as conn:
        readers = await conn.fetch("""
            SELECT r.*, u.first_name, u.last_name, u.email 
            FROM readers r 
            JOIN users u ON r.user_id = u.id 
            WHERE r.availability_status = 'online'
            ORDER BY r.updated_at DESC
        """)
        
        return [dict(reader) for reader in readers]

@app.get("/api/reader/profile")
async def get_reader_profile(user_data: dict = Depends(verify_clerk_token)):
    """Get reader profile for authenticated user"""
    user_id = user_data["user_id"]
    
    async with db_pool.acquire() as conn:
        reader = await conn.fetchrow("""
            SELECT r.*, u.first_name, u.last_name, u.email 
            FROM readers r 
            JOIN users u ON r.user_id = u.id 
            WHERE r.user_id = $1
        """, user_id)
        
        if not reader:
            raise HTTPException(status_code=404, detail="Reader profile not found")
        
        return dict(reader)

@app.put("/api/reader/status")
async def update_reader_status(
    status_update: ReaderStatus,
    user_data: dict = Depends(verify_clerk_token)
):
    """Update reader availability status and rates"""
    user_id = user_data["user_id"]
    
    async with db_pool.acquire() as conn:
        # Check if reader exists
        reader = await conn.fetchrow("SELECT id FROM readers WHERE user_id = $1", user_id)
        if not reader:
            raise HTTPException(status_code=404, detail="Reader profile not found")
        
        # Build update query
        update_fields = ["availability_status = $1", "updated_at = NOW()"]
        values = [status_update.availability_status]
        param_count = 2
        
        if status_update.chat_rate_per_minute is not None:
            update_fields.append(f"chat_rate_per_minute = ${param_count}")
            values.append(status_update.chat_rate_per_minute)
            param_count += 1
            
        if status_update.phone_rate_per_minute is not None:
            update_fields.append(f"phone_rate_per_minute = ${param_count}")
            values.append(status_update.phone_rate_per_minute)
            param_count += 1
            
        if status_update.video_rate_per_minute is not None:
            update_fields.append(f"video_rate_per_minute = ${param_count}")
            values.append(status_update.video_rate_per_minute)
            param_count += 1
        
        values.append(user_id)
        
        query = f"""
            UPDATE readers 
            SET {', '.join(update_fields)}
            WHERE user_id = ${param_count}
            RETURNING *
        """
        
        updated_reader = await conn.fetchrow(query, *values)
        
        # Notify connected clients of status change
        await broadcast_reader_status_change(dict(updated_reader))
        
        return dict(updated_reader)

@app.post("/api/session/request")
async def request_reading_session(
    session_request: SessionRequest,
    user_data: dict = Depends(verify_clerk_token)
):
    """Request a reading session with a reader"""
    user_id = user_data["user_id"]
    
    async with db_pool.acquire() as conn:
        # Get client info
        client = await conn.fetchrow("SELECT * FROM clients WHERE user_id = $1", user_id)
        if not client:
            raise HTTPException(status_code=404, detail="Client profile not found")
        
        # Get reader info and check availability
        reader = await conn.fetchrow("""
            SELECT * FROM readers 
            WHERE id = $1 AND availability_status = 'online'
        """, session_request.reader_id)
        
        if not reader:
            raise HTTPException(status_code=404, detail="Reader not available")
        
        # Get rate for session type
        rate_field = f"{session_request.session_type}_rate_per_minute"
        rate = reader[rate_field]
        
        if rate <= 0:
            raise HTTPException(status_code=400, detail=f"Reader does not offer {session_request.session_type} sessions")
        
        # Check client balance
        if client['balance'] < rate:
            raise HTTPException(status_code=400, detail="Insufficient balance")
        
        # Create session
        room_id = str(uuid.uuid4())
        session_id = await conn.fetchval("""
            INSERT INTO reading_sessions 
            (client_id, reader_id, session_type, rate_per_minute, room_id, status)
            VALUES ($1, $2, $3, $4, $5, 'pending')
            RETURNING id
        """, client['id'], session_request.reader_id, session_request.session_type, rate, room_id)
        
        # Get complete session info
        session = await conn.fetchrow("""
            SELECT rs.*, 
                   u1.first_name as client_first_name, u1.last_name as client_last_name,
                   u2.first_name as reader_first_name, u2.last_name as reader_last_name
            FROM reading_sessions rs
            JOIN clients c ON rs.client_id = c.id
            JOIN readers r ON rs.reader_id = r.id
            JOIN users u1 ON c.user_id = u1.id
            JOIN users u2 ON r.user_id = u2.id
            WHERE rs.id = $1
        """, session_id)
        
        # Notify reader of incoming session request
        await notify_reader_session_request(dict(session))
        
        return dict(session)

# WebSocket for real-time notifications
@app.websocket("/api/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    await websocket.accept()
    websocket_connections[user_id] = websocket
    
    try:
        while True:
            # Keep connection alive
            data = await websocket.receive_text()
            # Handle ping/pong or other messages
            await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        if user_id in websocket_connections:
            del websocket_connections[user_id]

# Helper functions for WebSocket notifications
async def broadcast_reader_status_change(reader_data: dict):
    """Notify all connected clients of reader status change"""
    message = {
        "type": "reader_status_change",
        "data": reader_data
    }
    
    # Send to all connected clients
    for user_id, websocket in websocket_connections.items():
        try:
            await websocket.send_text(json.dumps(message))
        except:
            # Remove stale connections
            del websocket_connections[user_id]

async def notify_reader_session_request(session_data: dict):
    """Notify reader of incoming session request"""
    reader_user_id = await get_reader_user_id(session_data['reader_id'])
    
    if reader_user_id and reader_user_id in websocket_connections:
        message = {
            "type": "session_request",
            "data": session_data
        }
        try:
            await websocket_connections[reader_user_id].send_text(json.dumps(message))
        except:
            # Remove stale connection
            del websocket_connections[reader_user_id]

async def get_reader_user_id(reader_id: str) -> Optional[str]:
    """Get user_id for a reader"""
    async with db_pool.acquire() as conn:
        result = await conn.fetchval("SELECT user_id FROM readers WHERE id = $1", reader_id)
        return result

# Session billing functions
async def start_session_billing(session_id: str, session_data: dict):
    """Start billing for an active session"""
    active_sessions[session_id] = {
        "session_id": session_id,
        "client_id": session_data['client_id'],
        "reader_id": session_data['reader_id'],
        "rate_per_minute": session_data['rate_per_minute'],
        "start_time": datetime.now(),
        "last_bill_time": datetime.now(),
        "total_billed": 0.0
    }
    
    # Start background billing task
    asyncio.create_task(bill_session_minutes(session_id))
    logger.info(f"Started billing for session {session_id}")

async def end_session_billing(session_id: str, session_data: dict) -> dict:
    """End billing for a session and finalize charges"""
    if session_id in active_sessions:
        billing_data = active_sessions[session_id]
        end_time = datetime.now()
        
        # Calculate final minutes and amount
        total_seconds = (end_time - billing_data['start_time']).total_seconds()
        total_minutes = total_seconds / 60.0
        total_amount = total_minutes * billing_data['rate_per_minute']
        
        # Apply any remaining partial minute billing
        await bill_partial_minute(session_id, end_time)
        
        async with db_pool.acquire() as conn:
            # Update session with final amounts
            await conn.execute("""
                UPDATE reading_sessions 
                SET status = 'completed', end_time = $1, total_minutes = $2, 
                    total_amount = $3, updated_at = NOW()
                WHERE id = $4
            """, end_time, total_minutes, total_amount, session_id)
            
            # Process revenue split (70% to reader, 30% to platform)
            reader_amount = total_amount * 0.7
            
            # Add earnings to reader (implement reader earnings table if needed)
            # For now, just log the transaction
            logger.info(f"Session {session_id} completed: ${total_amount:.2f} total, ${reader_amount:.2f} to reader")
            
            # Get updated session data
            updated_session = await conn.fetchrow("""
                SELECT * FROM reading_sessions WHERE id = $1
            """, session_id)
        
        # Clean up active session
        del active_sessions[session_id]
        
        return dict(updated_session)
    
    # If session wasn't actively billed, just mark as completed
    async with db_pool.acquire() as conn:
        await conn.execute("""
            UPDATE reading_sessions 
            SET status = 'completed', end_time = NOW(), updated_at = NOW()
            WHERE id = $1
        """, session_id)
        
        updated_session = await conn.fetchrow("""
            SELECT * FROM reading_sessions WHERE id = $1
        """, session_id)
        
        return dict(updated_session)

async def bill_session_minutes(session_id: str):
    """Background task to bill session per minute"""
    while session_id in active_sessions:
        try:
            await asyncio.sleep(60)  # Wait 1 minute
            
            if session_id not in active_sessions:
                break
                
            billing_data = active_sessions[session_id]
            current_time = datetime.now()
            
            # Bill for the minute
            await bill_partial_minute(session_id, current_time)
            
        except Exception as e:
            logger.error(f"Error in billing task for session {session_id}: {str(e)}")
            break

async def bill_partial_minute(session_id: str, current_time: datetime):
    """Bill for a partial or full minute"""
    if session_id not in active_sessions:
        return
        
    billing_data = active_sessions[session_id]
    rate_per_minute = billing_data['rate_per_minute']
    
    async with db_pool.acquire() as conn:
        # Check client balance
        client = await conn.fetchrow("""
            SELECT balance FROM clients 
            WHERE id = $1
        """, billing_data['client_id'])
        
        if not client or client['balance'] < rate_per_minute:
            # Insufficient funds - end session
            logger.warning(f"Insufficient funds for session {session_id}, ending session")
            
            # End the session due to insufficient funds
            await conn.execute("""
                UPDATE reading_sessions 
                SET status = 'completed', end_time = $1, updated_at = NOW()
                WHERE id = $2
            """, current_time, session_id)
            
            # Notify both parties
            session_info = await conn.fetchrow("""
                SELECT rs.*, c.user_id as client_user_id, r.user_id as reader_user_id
                FROM reading_sessions rs
                JOIN clients c ON rs.client_id = c.id
                JOIN readers r ON rs.reader_id = r.id
                WHERE rs.id = $1
            """, session_id)
            
            if session_info:
                await notify_session_update(session_info['client_user_id'], {
                    "type": "session_ended",
                    "reason": "insufficient_funds",
                    "session_id": session_id
                })
                await notify_session_update(session_info['reader_user_id'], {
                    "type": "session_ended", 
                    "reason": "insufficient_funds",
                    "session_id": session_id
                })
            
            # Remove from active sessions
            if session_id in active_sessions:
                del active_sessions[session_id]
            return
        
        # Deduct the minute charge
        await conn.execute("""
            UPDATE clients 
            SET balance = balance - $1, updated_at = NOW()
            WHERE id = $2
        """, rate_per_minute, billing_data['client_id'])
        
        # Update billing data
        billing_data['total_billed'] += rate_per_minute
        billing_data['last_bill_time'] = current_time
        
        logger.info(f"Billed ${rate_per_minute:.2f} for session {session_id}")

async def notify_session_update(user_id: str, message: dict):
    """Notify a user about session updates"""
    if user_id in websocket_connections:
        try:
            await websocket_connections[user_id].send_text(json.dumps(message))
        except:
            # Remove stale connection
            if user_id in websocket_connections:
                del websocket_connections[user_id]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
