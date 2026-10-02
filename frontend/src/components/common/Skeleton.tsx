export function Skeleton({ className = '' }: { className?: string }) {
  return (
    <div
      data-testid="skeleton"
      className={`animate-pulse bg-gray-200 dark:bg-gray-800 rounded ${className}`}
    />
  );
}

export function JobDetailSkeleton() {
  return (
    <div className="space-y-6 p-6" data-testid="job-detail-skeleton">
      <div className="space-y-2">
        <Skeleton className="h-7 w-2/3" />
        <Skeleton className="h-4 w-1/3" />
      </div>
      <div className="flex gap-4">
        <Skeleton className="h-10 w-24 rounded-full" />
        <Skeleton className="h-10 w-24 rounded-full" />
      </div>
      <div className="space-y-3">
        <Skeleton className="h-5 w-1/4" />
        <Skeleton className="h-24 w-full" />
      </div>
      <div className="space-y-3">
        <Skeleton className="h-5 w-1/4" />
        <Skeleton className="h-40 w-full" />
      </div>
    </div>
  );
}
