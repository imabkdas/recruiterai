import React, { createContext, useState, useCallback } from 'react';

export interface ToastAction {
  label: string;
  href?: string;
  onClick?: () => void;
}

export interface Toast {
  id: string;
  message: React.ReactNode;
  type?: 'success' | 'error' | 'info';
  action?: ToastAction;
}

export interface ToastContextValue {
  showToast: (
    message: React.ReactNode,
    type?: 'success' | 'error' | 'info',
    action?: ToastAction
  ) => void;
}

// eslint-disable-next-line react-refresh/only-export-components
export const ToastContext = createContext<ToastContextValue | undefined>(undefined);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const showToast = useCallback(
    (
      message: React.ReactNode,
      type: 'success' | 'error' | 'info' = 'success',
      action?: ToastAction
    ) => {
      const id = Math.random().toString(36).substring(2, 9);
      setToasts((prev) => [...prev, { id, message, type, action }]);

      setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== id));
      }, 4000);
    },
    []
  );

  return (
    <ToastContext.Provider value={{ showToast }}>
      {children}
      <div
        className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 pointer-events-none"
        aria-live="polite"
      >
        {toasts.map((toast) => (
          <div
            key={toast.id}
            role="status"
            className={`pointer-events-auto px-4 py-2.5 rounded-lg shadow-lg text-sm font-medium transition-all transform duration-200 flex items-center gap-2 ${
              toast.type === 'error'
                ? 'bg-red-600 text-white'
                : toast.type === 'info'
                ? 'bg-blue-600 text-white'
                : 'bg-emerald-600 text-white'
            }`}
          >
            <span>{toast.message}</span>
            {toast.action &&
              (toast.action.href ? (
                <a
                  href={toast.action.href}
                  className="font-bold underline hover:opacity-90 ml-1 text-white"
                >
                  {toast.action.label}
                </a>
              ) : (
                <button
                  type="button"
                  onClick={toast.action.onClick}
                  className="font-bold underline hover:opacity-90 ml-1 text-white"
                >
                  {toast.action.label}
                </button>
              ))}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
