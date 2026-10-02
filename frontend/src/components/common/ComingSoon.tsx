export function ComingSoon({ title }: { title: string }) {
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] p-8 text-center">
      <div className="p-4 rounded-2xl bg-indigo-50 dark:bg-indigo-950/40 text-indigo-600 dark:text-indigo-400 mb-4 text-3xl font-bold">
        🚀
      </div>
      <h2 className="text-2xl font-bold text-gray-900 dark:text-gray-100 mb-2">
        {title}
      </h2>
      <p className="text-sm text-gray-500 dark:text-gray-400 max-w-md">
        This view is coming soon in the next phase of JobPilot development.
      </p>
    </div>
  );
}
