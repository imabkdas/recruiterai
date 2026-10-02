interface EmptyStateProps {
  title?: string;
  message?: string;
}

export function EmptyState({
  title = 'No items found',
  message = 'There are no items to display at this time.',
}: EmptyStateProps) {
  return (
    <div
      data-testid="empty-state"
      className="flex flex-col items-center justify-center p-12 text-center text-gray-500 dark:text-gray-400"
    >
      <div className="w-12 h-12 flex items-center justify-center rounded-full bg-gray-100 dark:bg-gray-800 text-gray-400 dark:text-gray-500 mb-3 text-lg font-bold">
        ∅
      </div>
      <h3 className="text-base font-semibold text-gray-800 dark:text-gray-200 mb-1">
        {title}
      </h3>
      <p className="text-sm max-w-sm">{message}</p>
    </div>
  );
}
