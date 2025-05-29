import React, { useState, useEffect } from "react";
import {
  SignedIn,
  SignedOut,
  SignInButton,
  SignUpButton,
  UserButton,
  useUser,
  useAuth
} from "@clerk/clerk-react";
import axios from "axios";
import "./App.css";

// API Configuration
const API_BASE_URL = import.meta.env.REACT_APP_BACKEND_URL || process.env.REACT_APP_BACKEND_URL;

// Create axios instance with authentication
const createAuthenticatedAxios = (getToken) => {
  const instance = axios.create({
    baseURL: API_BASE_URL,
  });

  instance.interceptors.request.use(async (config) => {
    const token = await getToken();
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  });

  return instance;
};

// Components
function WelcomeScreen() {
  return (
    <div className="min-h-screen bg-gradient-to-br from-purple-900 via-black to-pink-900 flex items-center justify-center">
      <div className="text-center space-y-8 px-4">
        {/* Hero Image Placeholder */}
        <div className="w-64 h-64 mx-auto bg-gradient-to-br from-pink-500 to-purple-600 rounded-full flex items-center justify-center">
          <span className="text-6xl">🔮</span>
        </div>
        
        {/* Main Header */}
        <h1 className="text-6xl font-alex-brush text-pink-400 mb-4">
          SoulSeer
        </h1>
        
        {/* Tagline */}
        <p className="text-2xl font-playfair text-white mb-8">
          A Community of Gifted Psychics
        </p>
        
        {/* Authentication Buttons */}
        <div className="space-x-4">
          <SignInButton>
            <button className="bg-pink-500 hover:bg-pink-600 text-white px-8 py-3 rounded-lg font-playfair transition-colors">
              Sign In
            </button>
          </SignInButton>
          <SignUpButton>
            <button className="bg-purple-600 hover:bg-purple-700 text-white px-8 py-3 rounded-lg font-playfair transition-colors">
              Sign Up
            </button>
          </SignUpButton>
        </div>
      </div>
    </div>
  );
}

function Dashboard() {
  const { user } = useUser();
  const { getToken } = useAuth();
  const [userProfile, setUserProfile] = useState(null);
  const [availableReaders, setAvailableReaders] = useState([]);
  const [readerProfile, setReaderProfile] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (user) {
      loadUserData();
      loadAvailableReaders();
    }
  }, [user]);

  const loadUserData = async () => {
    try {
      const api = createAuthenticatedAxios(getToken);
      const response = await api.get('/api/user/profile');
      setUserProfile(response.data);
      
      // Try to load reader profile if user is a reader
      if (response.data.role === 'reader') {
        try {
          const readerResponse = await api.get('/api/reader/profile');
          setReaderProfile(readerResponse.data);
        } catch (err) {
          // Reader profile doesn't exist, that's okay
          console.log('No reader profile found');
        }
      }
    } catch (err) {
      setError('Failed to load user profile');
      console.error('Error loading user data:', err);
    } finally {
      setLoading(false);
    }
  };

  const loadAvailableReaders = async () => {
    try {
      const api = createAuthenticatedAxios(getToken);
      const response = await api.get('/api/readers/available');
      setAvailableReaders(response.data);
    } catch (err) {
      console.error('Error loading available readers:', err);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-purple-900 via-black to-pink-900 flex items-center justify-center">
        <div className="text-white text-xl">Loading...</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-purple-900 via-black to-pink-900">
      {/* Header */}
      <header className="bg-black/20 backdrop-blur-sm border-b border-pink-500/30">
        <div className="container mx-auto px-4 py-4 flex justify-between items-center">
          <h1 className="text-3xl font-alex-brush text-pink-400">SoulSeer</h1>
          <div className="flex items-center space-x-4">
            <span className="text-white font-playfair">
              Welcome, {user?.firstName || 'User'}
            </span>
            <UserButton />
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="container mx-auto px-4 py-8">
        {userProfile?.role === 'reader' ? (
          <ReaderDashboard 
            readerProfile={readerProfile} 
            userProfile={userProfile}
            onProfileUpdate={loadUserData}
            api={createAuthenticatedAxios(getToken)}
          />
        ) : (
          <ClientDashboard 
            availableReaders={availableReaders}
            userProfile={userProfile}
            api={createAuthenticatedAxios(getToken)}
          />
        )}
      </main>
    </div>
  );
}

function ReaderDashboard({ readerProfile, userProfile, onProfileUpdate, api }) {
  const [status, setStatus] = useState(readerProfile?.availability_status || 'offline');
  const [rates, setRates] = useState({
    chat: readerProfile?.chat_rate_per_minute || 0,
    phone: readerProfile?.phone_rate_per_minute || 0,
    video: readerProfile?.video_rate_per_minute || 0
  });
  const [updating, setUpdating] = useState(false);

  const updateStatus = async (newStatus) => {
    setUpdating(true);
    try {
      await api.put('/api/reader/status', {
        availability_status: newStatus,
        chat_rate_per_minute: rates.chat,
        phone_rate_per_minute: rates.phone,
        video_rate_per_minute: rates.video
      });
      setStatus(newStatus);
      onProfileUpdate();
    } catch (err) {
      console.error('Error updating status:', err);
      alert('Failed to update status');
    } finally {
      setUpdating(false);
    }
  };

  const updateRates = async () => {
    setUpdating(true);
    try {
      await api.put('/api/reader/status', {
        availability_status: status,
        chat_rate_per_minute: rates.chat,
        phone_rate_per_minute: rates.phone,
        video_rate_per_minute: rates.video
      });
      onProfileUpdate();
      alert('Rates updated successfully!');
    } catch (err) {
      console.error('Error updating rates:', err);
      alert('Failed to update rates');
    } finally {
      setUpdating(false);
    }
  };

  if (!readerProfile) {
    return (
      <div className="text-center text-white">
        <h2 className="text-2xl font-playfair mb-4">Reader Profile Not Found</h2>
        <p>Please contact an administrator to set up your reader profile.</p>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <h2 className="text-3xl font-alex-brush text-pink-400 text-center">
        Reader Dashboard
      </h2>

      {/* Status Control */}
      <div className="bg-black/40 backdrop-blur-sm rounded-lg p-6 border border-pink-500/30">
        <h3 className="text-xl font-playfair text-white mb-4">Availability Status</h3>
        <div className="flex space-x-4">
          {['offline', 'online', 'busy'].map((statusOption) => (
            <button
              key={statusOption}
              onClick={() => updateStatus(statusOption)}
              disabled={updating}
              className={`px-6 py-2 rounded-lg font-playfair transition-colors ${
                status === statusOption
                  ? 'bg-pink-500 text-white'
                  : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
              } ${updating ? 'opacity-50 cursor-not-allowed' : ''}`}
            >
              {statusOption.charAt(0).toUpperCase() + statusOption.slice(1)}
            </button>
          ))}
        </div>
        <div className="mt-2">
          <span className={`inline-block w-3 h-3 rounded-full mr-2 ${
            status === 'online' ? 'bg-green-500' : 
            status === 'busy' ? 'bg-yellow-500' : 'bg-red-500'
          }`}></span>
          <span className="text-white font-playfair">
            Currently {status}
          </span>
        </div>
      </div>

      {/* Rate Settings */}
      <div className="bg-black/40 backdrop-blur-sm rounded-lg p-6 border border-pink-500/30">
        <h3 className="text-xl font-playfair text-white mb-4">Per-Minute Rates</h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {['chat', 'phone', 'video'].map((type) => (
            <div key={type}>
              <label className="block text-white font-playfair mb-2 capitalize">
                {type} Rate ($/min)
              </label>
              <input
                type="number"
                step="0.01"
                min="0"
                value={rates[type]}
                onChange={(e) => setRates({...rates, [type]: parseFloat(e.target.value) || 0})}
                className="w-full px-4 py-2 bg-gray-800 text-white rounded-lg border border-gray-600 focus:border-pink-500 focus:outline-none"
              />
            </div>
          ))}
        </div>
        <button
          onClick={updateRates}
          disabled={updating}
          className="mt-4 bg-purple-600 hover:bg-purple-700 text-white px-6 py-2 rounded-lg font-playfair transition-colors disabled:opacity-50"
        >
          {updating ? 'Updating...' : 'Update Rates'}
        </button>
      </div>

      {/* Session History Placeholder */}
      <div className="bg-black/40 backdrop-blur-sm rounded-lg p-6 border border-pink-500/30">
        <h3 className="text-xl font-playfair text-white mb-4">Recent Sessions</h3>
        <p className="text-gray-400">No recent sessions found.</p>
      </div>
    </div>
  );
}

function ClientDashboard({ availableReaders, userProfile, api }) {
  const [selectedReader, setSelectedReader] = useState(null);
  const [sessionType, setSessionType] = useState('chat');
  const [requesting, setRequesting] = useState(false);

  const requestSession = async (readerId, type) => {
    setRequesting(true);
    try {
      const response = await api.post('/api/session/request', {
        reader_id: readerId,
        session_type: type
      });
      alert('Session request sent! Waiting for reader to accept...');
      console.log('Session created:', response.data);
    } catch (err) {
      console.error('Error requesting session:', err);
      alert(err.response?.data?.detail || 'Failed to request session');
    } finally {
      setRequesting(false);
    }
  };

  return (
    <div className="space-y-8">
      <h2 className="text-3xl font-alex-brush text-pink-400 text-center">
        Available Readers
      </h2>

      {/* Available Readers */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {availableReaders.map((reader) => (
          <div key={reader.id} className="bg-black/40 backdrop-blur-sm rounded-lg p-6 border border-pink-500/30">
            <div className="text-center mb-4">
              <h3 className="text-xl font-playfair text-white">
                {reader.first_name} {reader.last_name}
              </h3>
              <div className="flex items-center justify-center mt-2">
                <span className="w-3 h-3 bg-green-500 rounded-full mr-2"></span>
                <span className="text-green-400 font-playfair">Online</span>
              </div>
            </div>

            {/* Rates */}
            <div className="space-y-2 mb-4">
              <div className="flex justify-between text-sm">
                <span className="text-gray-300">Chat:</span>
                <span className="text-white">${reader.chat_rate_per_minute}/min</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-gray-300">Phone:</span>
                <span className="text-white">${reader.phone_rate_per_minute}/min</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-gray-300">Video:</span>
                <span className="text-white">${reader.video_rate_per_minute}/min</span>
              </div>
            </div>

            {/* Action Buttons */}
            <div className="space-y-2">
              {['chat', 'phone', 'video'].map((type) => {
                const rate = reader[`${type}_rate_per_minute`];
                return rate > 0 ? (
                  <button
                    key={type}
                    onClick={() => requestSession(reader.id, type)}
                    disabled={requesting}
                    className="w-full bg-pink-600 hover:bg-pink-700 text-white py-2 px-4 rounded-lg font-playfair transition-colors disabled:opacity-50 capitalize"
                  >
                    Start {type} Reading (${rate}/min)
                  </button>
                ) : null;
              })}
            </div>
          </div>
        ))}
      </div>

      {availableReaders.length === 0 && (
        <div className="text-center text-gray-400 py-8">
          <p className="text-xl font-playfair">No readers are currently available.</p>
          <p>Please check back later.</p>
        </div>
      )}

      {/* User Balance */}
      <div className="bg-black/40 backdrop-blur-sm rounded-lg p-6 border border-pink-500/30">
        <h3 className="text-xl font-playfair text-white mb-4">Account Balance</h3>
        <div className="flex justify-between items-center">
          <span className="text-2xl font-playfair text-pink-400">
            ${userProfile?.balance || '0.00'}
          </span>
          <button className="bg-green-600 hover:bg-green-700 text-white px-6 py-2 rounded-lg font-playfair transition-colors">
            Add Funds
          </button>
        </div>
      </div>
    </div>
  );
}

function App() {
  return (
    <div className="App">
      <SignedOut>
        <WelcomeScreen />
      </SignedOut>
      <SignedIn>
        <Dashboard />
      </SignedIn>
    </div>
  );
}

export default App;
