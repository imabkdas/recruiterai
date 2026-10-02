import React, { useState, useEffect } from 'react';
import { useFocusTrap } from '../../hooks/useFocusTrap';

export const APPLICATION_CHANNELS = [
  { value: 'linkedin', label: 'LinkedIn' },
  { value: 'naukri', label: 'Naukri' },
  { value: 'company_site', label: 'Company Site' },
  { value: 'referral', label: 'Referral' },
  { value: 'email', label: 'Email' },
  { value: 'other', label: 'Other' },
] as const;

interface MarkAppliedDialogProps {
  isOpen: boolean;
  jobId?: number;
  company: string;
  title: string;
  onClose: () => void;
  onSubmit: (data: { channel: string; referral_contact?: string | null; note?: string | null }) => void;
  isSubmitting?: boolean;
}

export function MarkAppliedDialog({
  isOpen,
  company,
  title,
  onClose,
  onSubmit,
  isSubmitting = false,
}: MarkAppliedDialogProps) {
  const [channel, setChannel] = useState<string>('linkedin');
  const [referralContact, setReferralContact] = useState<string>('');
  const [note, setNote] = useState<string>('');

  const containerRef = useFocusTrap(isOpen, onClose);

  // Reset fields when opening
  useEffect(() => {
    if (isOpen) {
      setChannel('linkedin');
      setReferralContact('');
      setNote('');
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit({
      channel,
      referral_contact: referralContact.trim() || null,
      note: note.trim() || null,
    });
  };

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
        aria-labelledby="mark-applied-title"
        className="w-full max-w-lg bg-white dark:bg-gray-900 rounded-xl shadow-2xl border border-gray-200 dark:border-gray-800 p-6 space-y-5"
      >
        <div className="flex items-start justify-between">
          <div>
            <h2
              id="mark-applied-title"
              className="text-lg font-bold text-gray-900 dark:text-gray-100"
            >
              Mark as Applied
            </h2>
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
              {company} — {title}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close dialog"
            className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 p-1 rounded-md focus:outline-none focus:ring-2 focus:ring-indigo-500"
          >
            ✕
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Channel Select */}
          <div className="space-y-1">
            <label
              htmlFor="channel-select"
              className="block text-xs font-semibold text-gray-700 dark:text-gray-300"
            >
              Application Channel <span className="text-red-500">*</span>
            </label>
            <select
              id="channel-select"
              value={channel}
              onChange={(e) => setChannel(e.target.value)}
              className="w-full px-3 py-2 text-sm bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded-lg text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
            >
              {APPLICATION_CHANNELS.map((ch) => (
                <option key={ch.value} value={ch.value}>
                  {ch.label}
                </option>
              ))}
            </select>
          </div>

          {/* Referral Contact Input */}
          <div className="space-y-1">
            <label
              htmlFor="referral-contact-input"
              className="block text-xs font-semibold text-gray-700 dark:text-gray-300"
            >
              Referral Contact <span className="text-xs font-normal text-gray-400">(optional)</span>
            </label>
            <input
              id="referral-contact-input"
              type="text"
              placeholder="e.g., Alex Smith, Senior Staff Engineer"
              value={referralContact}
              onChange={(e) => setReferralContact(e.target.value)}
              className="w-full px-3 py-2 text-sm bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded-lg text-gray-900 dark:text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
            />
          </div>

          {/* Note Textarea */}
          <div className="space-y-1">
            <label
              htmlFor="note-input"
              className="block text-xs font-semibold text-gray-700 dark:text-gray-300"
            >
              Application Note <span className="text-xs font-normal text-gray-400">(optional)</span>
            </label>
            <textarea
              id="note-input"
              rows={3}
              placeholder="e.g., Applied via internal referral portal with custom short note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              className="w-full px-3 py-2 text-sm bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded-lg text-gray-900 dark:text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 resize-none"
            />
          </div>

          {/* Dialog Action Buttons */}
          <div className="flex items-center justify-end gap-3 pt-3 border-t border-gray-100 dark:border-gray-800">
            <button
              type="button"
              onClick={onClose}
              disabled={isSubmitting}
              aria-label="Cancel application dialog"
              className="px-4 py-2 text-xs font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSubmitting}
              aria-label="Submit mark as applied"
              className="inline-flex items-center justify-center gap-1.5 px-4 py-2 text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 rounded-lg shadow-xs transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 dark:focus:ring-offset-gray-900"
            >
              {isSubmitting ? 'Saving...' : 'Mark Applied'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
