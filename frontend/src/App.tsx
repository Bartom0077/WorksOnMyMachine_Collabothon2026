import { Suspense, lazy, useCallback, useState } from "react";
import { type ApiError, api, toApiError } from "./api/client";
import { ApprovalDialog } from "./components/ApprovalDialog";
import { ChatPanel } from "./components/ChatPanel";
import { ConfirmDialog } from "./components/ConfirmDialog";
import { DecisionHistory } from "./components/DecisionHistory";
import { ExplanationCard } from "./components/ExplanationCard";
import { ErrorNotice, Skeleton } from "./components/Feedback";
import { Icon } from "./components/Icon";
import { OptionsSection } from "./components/OptionsSection";
import { OutcomeBanner } from "./components/OutcomeBanner";
import { PlansList } from "./components/PlansList";
import { StatusHero } from "./components/StatusHero";
import { TopBar } from "./components/TopBar";
import { useAsyncResource } from "./hooks/useAsyncResource";
import { useChat } from "./hooks/useChat";
import { useDecisionFlow } from "./hooks/useDecisionFlow";
import { formatDate } from "./lib/format";
import type { Dashboard, Recommendation, ScenarioParams } from "./types/api";

// The charting library is the bulk of the bundle: it is a separate chunk, fetched in parallel with the
// first dashboard request instead of blocking first paint.
const balanceChartModule = import("./components/BalanceChart");
const BalanceChart = lazy(() => balanceChartModule);

const DEFAULT_PARAMS: ScenarioParams = { as_of_date: "2025-05-10", horizon_days: 60, buffer_eur: 3000 };

/** The LLM's pick when it names an option that is actually on offer, otherwise the code ranking. */
function pickRecommendedOptionId(dashboard: Dashboard, recommendation: Recommendation | null): string | null {
  const llmChoice = recommendation?.recommended_option_id ?? null;
  if (llmChoice !== null && dashboard.options.some((option) => option.id === llmChoice)) return llmChoice;
  return dashboard.code_recommended_option_id;
}

function ChartSkeleton() {
  return (
    <div className="card">
      <Skeleton label="Loading the forecast chart" lines={1} block />
    </div>
  );
}

function DashboardSkeleton() {
  return (
    <>
      <div className="card">
        <Skeleton label="Loading your cash position" lines={2} />
      </div>
      <ChartSkeleton />
    </>
  );
}

export default function App() {
  const [params, setParams] = useState<ScenarioParams>(DEFAULT_PARAMS);
  const [selectedOptionId, setSelectedOptionId] = useState<string | null>(null);
  const [resetOpen, setResetOpen] = useState(false);
  const [resetBusy, setResetBusy] = useState(false);
  const [resetError, setResetError] = useState<ApiError | null>(null);

  const loadDashboard = useCallback((signal: AbortSignal) => api.getDashboard(params, signal), [params]);
  const dashboard = useAsyncResource(loadDashboard, { keepPreviousData: true });
  const events = useAsyncResource(api.getEvents, { keepPreviousData: true });
  const decisionLog = useAsyncResource(api.getDecisions, { keepPreviousData: true });

  // The (slow) LLM explanation is requested only once a dashboard has rendered, and again for every new one.
  const board = dashboard.data;
  const loadRecommendation = useCallback(
    (signal: AbortSignal) => {
      if (board === null) return Promise.reject(new Error("Dashboard not loaded"));
      const { as_of_date, horizon_days, buffer_eur } = board;
      return api.postRecommendation({ as_of_date, horizon_days, buffer_eur }, signal);
    },
    [board],
  );
  const recommendation = useAsyncResource(loadRecommendation, { enabled: board !== null });

  const reloadDashboard = dashboard.reload;
  const reloadEvents = events.reload;
  const reloadDecisions = decisionLog.reload;
  const refreshAll = useCallback(() => {
    reloadDashboard();
    reloadEvents();
    reloadDecisions();
  }, [reloadDashboard, reloadEvents, reloadDecisions]);

  const chat = useChat(params, refreshAll);
  const decisions = useDecisionFlow(params, recommendation.data?.explanation ?? null, refreshAll);
  const { startNewConversation } = chat;
  const { dismissOutcome } = decisions;

  const changeParams = useCallback(
    (patch: Partial<ScenarioParams>) => {
      setParams((current) => ({ ...current, ...patch }));
      setSelectedOptionId(null);
      dismissOutcome();
      if (patch.as_of_date !== undefined && patch.as_of_date !== params.as_of_date) {
        startNewConversation(`“Today” is now ${formatDate(patch.as_of_date)}. Started a new conversation.`);
      }
    },
    [params.as_of_date, startNewConversation, dismissOutcome],
  );

  async function resetDemo() {
    setResetBusy(true);
    setResetError(null);
    try {
      await api.resetDemo();
      setResetOpen(false);
      setSelectedOptionId(null);
      dismissOutcome();
      startNewConversation("Demo reset: all plans and decisions were deleted.");
      refreshAll();
    } catch (error) {
      setResetError(toApiError(error));
    } finally {
      setResetBusy(false);
    }
  }

  const recommendedOptionId = board ? pickRecommendedOptionId(board, recommendation.data) : null;
  const previewOptionId = selectedOptionId ?? recommendedOptionId;
  const previewOption = board?.options.find((option) => option.id === previewOptionId) ?? null;

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <TopBar
        params={params}
        dataRange={board?.data_range ?? null}
        onChange={changeParams}
        onResetDemo={() => setResetOpen(true)}
      />

      <div className="layout">
        <main id="main" className="layout__main" aria-busy={dashboard.loading}>
          <a className="assistant-link" href="#assistant">
            <Icon name="chat" />
            Tell the assistant about your plans
          </a>
          <OutcomeBanner outcome={decisions.outcome} onDismiss={decisions.dismissOutcome} />

          {dashboard.error ? (
            <ErrorNotice title="Your forecast could not be loaded" error={dashboard.error} onRetry={dashboard.reload} />
          ) : null}
          {board === null && dashboard.loading ? <DashboardSkeleton /> : null}
          {board !== null ? (
            <>
              <StatusHero dashboard={board} headline={recommendation.data?.headline ?? board.headline} />
              <Suspense fallback={<ChartSkeleton />}>
                <BalanceChart dashboard={board} previewOption={previewOption} />
              </Suspense>
              <ExplanationCard
                recommendation={recommendation}
                riskDrivers={board.risk_drivers}
                riskWindow={board.risk.risk_window}
              />
              <OptionsSection
                dashboard={board}
                recommendation={recommendation.data}
                recommendedOptionId={recommendedOptionId}
                previewOptionId={previewOption?.id ?? null}
                decisions={decisions}
                onPreview={setSelectedOptionId}
              />
            </>
          ) : null}

          <PlansList events={events} onRemoved={refreshAll} />
          <DecisionHistory decisions={decisionLog} />
        </main>

        <ChatPanel chat={chat} />
      </div>

      <ApprovalDialog decisions={decisions} />
      <ConfirmDialog
        open={resetOpen}
        title="Reset the demo?"
        confirmLabel="Delete plans and decisions"
        busyLabel="Resetting…"
        busy={resetBusy}
        error={resetError}
        danger
        onConfirm={() => void resetDemo()}
        onCancel={() => {
          setResetOpen(false);
          setResetError(null);
        }}
      >
        <p>
          This deletes <strong>all planned events and all recorded decisions</strong> so the demo can start from
          scratch. Bank transactions are not affected.
        </p>
      </ConfirmDialog>
    </div>
  );
}
