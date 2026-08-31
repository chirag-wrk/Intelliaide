import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from 'react';

interface UserState {
  name: string;
  email: string;
  role: 'user' | 'admin';
}

interface UserContextValue {
  user: UserState | null;
  loading: boolean;
  isAdmin: boolean;
  logout: () => void;
  refresh: () => Promise<void>;
}

const UserContext = createContext<UserContextValue | null>(null);

const API_BASE = import.meta.env.VITE_API_URL || '/api';

export function UserProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserState | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchIdentity = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/whoami`, { credentials: 'same-origin' });
      if (!res.ok) {
        setUser(null);
        return;
      }
      const data = await res.json();
      if (data.name && data.name !== 'anonymous') {
        setUser({
          name: data.name,
          email: data.email || '',
          role: data.is_admin ? 'admin' : 'user',
        });
      } else {
        setUser(null);
      }
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchIdentity(); }, [fetchIdentity]);

  const logout = useCallback(() => {
    window.location.href = '/oauth/sign_in';
  }, []);

  const isAdmin = user?.role === 'admin';

  return (
    <UserContext.Provider value={{ user, loading, isAdmin, logout, refresh: fetchIdentity }}>
      {children}
    </UserContext.Provider>
  );
}

export function useUser(): UserContextValue {
  const ctx = useContext(UserContext);
  if (!ctx) throw new Error('useUser must be used within UserProvider');
  return ctx;
}
