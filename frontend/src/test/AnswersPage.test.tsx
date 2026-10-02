import { screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { AnswersPage } from '../pages/AnswersPage';
import { renderWithProviders } from './test-utils';
import { api } from '../api/client';
import type { AnswerBankItem } from '../api/types';

const mockBankItems: AnswerBankItem[] = [
  {
    id: 1,
    question_norm: 'what is your notice period',
    category: 'setting',
    answer: 'NEEDS YOUR INPUT',
    approved: false,
  },
  {
    id: 2,
    question_norm: 'why do you want this role',
    category: 'free_text',
    answer: 'I want to build backend systems.',
    approved: false,
  },
];

describe('AnswersPage (Phase 5c-2b)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(api, 'getQueue').mockResolvedValue([]);
  });

  it('renders saved personal answers', async () => {
    vi.spyOn(api, 'getAnswers').mockResolvedValue(mockBankItems);
    renderWithProviders(<AnswersPage />);

    await waitFor(() => {
      expect(screen.getByText('what is your notice period')).toBeInTheDocument();
    });
    expect(screen.getByText('NEEDS YOUR INPUT')).toBeInTheDocument();
    expect(screen.getByText('why do you want this role')).toBeInTheDocument();
  });

  it('prominently displays NEEDS YOUR INPUT banner and supplies answer via PUT /api/answers/{id}', async () => {
    vi.spyOn(api, 'getAnswers').mockResolvedValue([]);
    vi.spyOn(api, 'askQuestion').mockResolvedValue({
      question: 'What is your notice period?',
      question_norm: 'what is your notice period',
      category: 'setting',
      answer: 'NEEDS YOUR INPUT',
      is_draft: true,
      answer_bank_id: 9,
      from_bank: false,
    });
    const setSpy = vi.spyOn(api, 'setAnswer').mockResolvedValue({ success: true });

    renderWithProviders(<AnswersPage />);
    await waitFor(() => {
      expect(screen.getByLabelText('Application question')).toBeInTheDocument();
    });
    fireEvent.change(screen.getByLabelText('Application question'), {
      target: { value: 'What is your notice period?' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Get answer' }));

    await waitFor(() => {
      expect(screen.getByText('NEEDS YOUR INPUT')).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText('Your answer'), { target: { value: '30 days' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save answer' }));

    await waitFor(() => {
      expect(setSpy).toHaveBeenCalledWith(9, { answer_text: '30 days' });
    });
  });

  it('approves an unapproved answer bank item via POST /api/answers/{id}/approve', async () => {
    vi.spyOn(api, 'getAnswers').mockResolvedValue(mockBankItems);
    const approveSpy = vi.spyOn(api, 'approveAnswer').mockResolvedValue({ success: true });

    renderWithProviders(<AnswersPage />);
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Approve why do you want this role' })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Approve why do you want this role' }));

    await waitFor(() => {
      expect(approveSpy).toHaveBeenCalledWith(2);
    });
  });

  it('edits an answer bank item and saves with PUT /api/answers/{id}', async () => {
    vi.spyOn(api, 'getAnswers').mockResolvedValue(mockBankItems);
    const setSpy = vi.spyOn(api, 'setAnswer').mockResolvedValue({ success: true });

    renderWithProviders(<AnswersPage />);
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Edit why do you want this role' })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Edit why do you want this role' }));
    fireEvent.change(screen.getByLabelText('Edit answer for why do you want this role'), {
      target: { value: 'I build Java services.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      expect(setSpy).toHaveBeenCalledWith(2, { answer_text: 'I build Java services.' });
    });
  });

  it('renders empty state when the answer bank is empty', async () => {
    vi.spyOn(api, 'getAnswers').mockResolvedValue([]);
    renderWithProviders(<AnswersPage />);
    await waitFor(() => {
      expect(screen.getByText('No saved answers yet')).toBeInTheDocument();
    });
  });

  it('displays error state and handles retry when getAnswers fails', async () => {
    const getAnswersSpy = vi
      .spyOn(api, 'getAnswers')
      .mockRejectedValueOnce(new Error('Server unavailable'))
      .mockResolvedValueOnce([]);
    renderWithProviders(<AnswersPage />);

    await waitFor(() => {
      expect(screen.getByText('Failed to load answers')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: /Retry/i }));
    await waitFor(() => {
      expect(getAnswersSpy).toHaveBeenCalledTimes(2);
      expect(screen.getByText('No saved answers yet')).toBeInTheDocument();
    });
  });
});
