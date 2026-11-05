import { AuthGate } from "@/components/auth-gate";
import { ContractUploadPanel } from "@/components/contract-upload-panel";

export default function AnalyzePage() {
  return (
    <AuthGate>
      <main className="w-full flex-1 overflow-y-auto px-4 py-8 sm:px-6 sm:py-10">
        <ContractUploadPanel />
      </main>
    </AuthGate>
  );
}
