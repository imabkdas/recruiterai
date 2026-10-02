import { useEffect, useRef } from 'react';

const FOCUSABLE_SELECTOR =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function useFocusTrap(isOpen: boolean, onClose?: () => void) {
  const containerRef = useRef<HTMLDivElement>(null);
  const previousActiveElementRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    // Save element that was focused before opening dialog
    previousActiveElementRef.current = document.activeElement as HTMLElement | null;

    const container = containerRef.current;
    if (container) {
      const focusables = container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR);
      if (focusables.length > 0) {
        // Focus first interactive control
        focusables[0].focus();
      } else {
        container.focus();
      }
    }

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        onClose?.();
        return;
      }

      if (e.key === 'Tab') {
        const currentContainer = containerRef.current;
        if (!currentContainer) return;

        const focusableElements = Array.from(
          currentContainer.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)
        );

        if (focusableElements.length === 0) {
          e.preventDefault();
          return;
        }

        const firstEl = focusableElements[0];
        const lastEl = focusableElements[focusableElements.length - 1];

        if (e.shiftKey) {
          // Shift + Tab
          if (document.activeElement === firstEl || !currentContainer.contains(document.activeElement)) {
            e.preventDefault();
            lastEl.focus();
          }
        } else {
          // Tab
          if (document.activeElement === lastEl || !currentContainer.contains(document.activeElement)) {
            e.preventDefault();
            firstEl.focus();
          }
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown, true);

    return () => {
      window.removeEventListener('keydown', handleKeyDown, true);
      // Return focus to previously active element on close
      if (previousActiveElementRef.current && typeof previousActiveElementRef.current.focus === 'function') {
        previousActiveElementRef.current.focus();
      }
    };
  }, [isOpen, onClose]);

  return containerRef;
}
