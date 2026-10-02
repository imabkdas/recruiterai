import { useFocusTrap } from '../../hooks/useFocusTrap';

interface ShortcutHelpDialogProps {
  isOpen: boolean;
  onClose: () => void;
}

const SHORTCUTS = [
  { key: 'j', desc: 'Move selection to next job in queue' },
  { key: 'k', desc: 'Move selection to previous job in queue' },
  { key: 'o', desc: 'Open job URL in a new tab' },
  { key: 'p', desc: 'Prepare application draft materials' },
  { key: 'a', desc: 'Open Mark Applied dialog' },
  { key: 's', desc: 'Skip job from queue' },
  { key: '?', desc: 'Show this keyboard shortcut help' },
  { key: 'Esc', desc: 'Close dialog / overlay' },
];

export function ShortcutHelpDialog({ isOpen, onClose }: ShortcutHelpDialogProps) {
  const containerRef = useFocusTrap(isOpen, onClose);

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-xs"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={containerRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="shortcut-help-title"
        className="w-full max-w-md bg-white dark:bg-gray-900 rounded-xl shadow-2xl border border-gray-200 dark:border-gray-800 p-6 space-y-4"
      >
        <div className="flex items-start justify-between">
          <h2
            id="shortcut-help-title"
            className="text-lg font-bold text-gray-900 dark:text-gray-100"
          >
            Keyboard Shortcuts
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close dialog"
            className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 p-1 rounded-md focus:outline-none focus:ring-2 focus:ring-indigo-500"
          >
            ✕
          </button>
        </div>

        <div className="divide-y divide-gray-100 dark:divide-gray-800">
          {SHORTCUTS.map((item) => (
            <div key={item.key} className="py-2.5 flex items-center justify-between text-xs">
              <span className="text-gray-600 dark:text-gray-400">{item.desc}</span>
              <kbd className="px-2 py-1 font-mono text-[11px] font-semibold text-gray-800 dark:text-gray-200 bg-gray-100 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded shadow-2xs">
                {item.key}
              </kbd>
            </div>
          ))}
        </div>

        <div className="pt-2 flex justify-end">
          <button
            type="button"
            onClick={onClose}
            aria-label="Close keyboard shortcuts dialog"
            className="px-4 py-2 text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-700 rounded-lg shadow-xs transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500"
          >
            Got it
          </button>
        </div>
      </div>
    </div>
  );
}
