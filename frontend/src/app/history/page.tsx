import { AuthGate } from "@/components/auth-gate";
import { HistoryList } from "@/components/history-list";

export default function HistoryPage() {
  return (
    <AuthGate>
      <main className="w-full flex-1 overflow-y-auto px-4 py-6 sm:px-6 sm:py-8">
        <div className="mx-auto max-w-5xl">
          <HistoryList />
        </div>
      </main>
    </AuthGate>
  );
}
