interface ErrorStateProps {
  title?: string;
  message?: string;
  onRetry?: () => void;
}

export function ErrorState({
  title = 'Something went wrong',
  message = 'Failed to load data from the server.',
  onRetry,
}: ErrorStateProps) {
  return (
    <div
      role="alert"
      className="flex flex-col items-center justify-center p-8 text-center bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-900 rounded-xl m-4"
    >
      <div className="w-12 h-12 flex items-center justify-center rounded-full bg-red-100 dark:bg-red-900/50 text-red-600 dark:text-red-400 mb-3 text-xl font-bold">
        !
      </div>
      <h3 className="text-base font-semibold text-red-900 dark:text-red-200 mb-1">
        {title}
      </h3>
      <p className="text-sm text-red-700 dark:text-red-400 max-w-md mb-4">
        {message}
      </p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="px-4 py-2 bg-red-600 hover:bg-red-700 text-white text-sm font-medium rounded-lg transition-colors shadow-sm focus:outline-none focus:ring-2 focus:ring-red-500 focus:ring-offset-2"
        >
          Retry
        </button>
      )}
    </div>
  );
}
