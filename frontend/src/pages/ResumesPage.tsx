import { useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { ResumeWorkbench } from '../components/today/ResumeWorkbench';

export function ResumesPage() {
  const [searchParams, setSearchParams] = useSearchParams();

  useEffect(() => {
    if (searchParams.toString()) {
      setSearchParams({}, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  return (
    <div className="h-full min-h-0 overflow-y-auto p-4">
      <ResumeWorkbench />
    </div>
  );
}
