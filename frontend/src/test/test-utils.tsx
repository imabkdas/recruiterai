import React from 'react';
import { render, RenderOptions } from '@testing-library/react';
import { MemoryRouter, MemoryRouterProps } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ToastProvider } from '../context/ToastContext';

interface WrapperOptions extends RenderOptions {
  routerProps?: MemoryRouterProps;
  queryClient?: QueryClient;
}

export function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
    },
  });
}

export function renderWithProviders(
  ui: React.ReactElement,
  options: WrapperOptions = {}
) {
  const {
    routerProps = { initialEntries: ['/'] },
    queryClient = createTestQueryClient(),
    ...renderOptions
  } = options;

  function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <MemoryRouter {...routerProps}>{children}</MemoryRouter>
        </ToastProvider>
      </QueryClientProvider>
    );
  }

  return {
    ...render(ui, { wrapper: Wrapper, ...renderOptions }),
    queryClient,
  };
}
