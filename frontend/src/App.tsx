import { Suspense, lazy, useCallback, useMemo, useRef, useState } from "react";
import { type ApiError, api, toApiError } from "./api/client";
import { AdviceSection } from "./components/AdviceSection";
import { ApprovalDialog } from "./components/ApprovalDialog";
import { ChatPanel } from "./components/ChatPanel";
import { ConfirmDialog } from "./components/ConfirmDialog";
import { DecisionHistory } from "./components/DecisionHistory";
import { ErrorNotice, Skeleton } from "./components/Feedback";
import { Icon } from "./components/Icon";
import { OptionsSection } from "./components/OptionsSection";
import { OutcomeBanner } from "./components/OutcomeBanner";
import { PlansList } from "./components/PlansList";
import { StatusHero } from "./components/StatusHero";
import { TopBar } from "./components/TopBar";
import { type WorkspaceTab, WorkspaceTabs } from "./components/WorkspaceTabs";
import { useAdviceFlow } from "./hooks/useAdviceFlow";
import { useAssistantPanel } from "./hooks/useAssistantPanel";
import { useAsyncResource } from "./hooks/useAsyncResource";
import { useChartPreview } from "./hooks/useChartPreview";
import { chatAttention, useChat } from "./hooks/useChat";
import { type DecisionOutcome, useDecisionFlow } from "./hooks/useDecisionFlow";
import { usePlanRemoval } from "./hooks/usePlanRemoval";
import { buildAdviceCards } from "./lib/advice";
import { formatDate } from "./lib/format";
import { isSingleColumn } from "./lib/layout";
import type { Dashboard, Recommendation, ScenarioParams } from "./types/api";

// The charting library is the bulk of the bundle: it is a separate chunk, fetched in parallel with the
// first dashboard request instead of blocking first paint.
const balanceChartModule = import("./components/BalanceChart");
const BalanceChart = lazy(() => balanceChartModule);

const RESET_OUTCOME = { title: "Demo reset: all plans and decisions were deleted.", messages: [] };

const DEFAULT_PARAMS: ScenarioParams = { as_of_date: "2025-05-10", horizon_days: 60, buffer_eur: 3000 };

/** The LLM's pick when it names an option that is actually on offer, otherwise the code ranking. */
function pickRecommendedOptionId(dashboard: Dashboard, recommendation: Recommendation | null): string | null {
  const llmChoice = recommendation?.recommended_option_id ?? null;
  if (llmChoice !== null && dashboard.options.some((option) => option.id === llmChoice)) return llmChoice;
  return dashboard.code_recommended_option_id;
}

type WorkspaceTabId = "options" | "advice" | "plans" | "history";

/** The tab on screen: the owner's choice while that tab exists, otherwise the first one (the most urgent). */
function pickActiveTab(tabs: WorkspaceTab<WorkspaceTabId>[], chosen: WorkspaceTabId | null): WorkspaceTabId {
  if (chosen !== null && tabs.some((tab) => tab.id === chosen)) return chosen;
  return tabs[0]?.id ?? "plans";
}

function optionOutcome(outcome: DecisionOutcome | null) {
  if (outcome === null) return null;
  return { title: `Option ${outcome.optionId} approved: ${outcome.optionTitle}`, messages: outcome.messages };
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
  const [resetOpen, setResetOpen] = useState(false);
  const [resetBusy, setResetBusy] = useState(false);
  const [resetError, setResetError] = useState<ApiError | null>(null);
  // Announced in the main column: the note in the chat is out of sight while the assistant is closed.
  const [resetDone, setResetDone] = useState(false);
  const [chosenTab, setChosenTab] = useState<WorkspaceTabId | null>(null);

  const loadDashboard = useCallback((signal: AbortSignal) => api.getDashboard(params, signal), [params]);
  const dashboard = useAsyncResource(loadDashboard, { keepPreviousData: true });
  // The CFO recommendations belong to the same scenario as the dashboard and are refreshed together with it.
  const loadAdvice = useCallback((signal: AbortSignal) => api.getAdvice(params, signal), [params]);
  const advice = useAsyncResource(loadAdvice, { keepPreviousData: true });
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

  // Like the explanation: the advisors' (slow) LLM wording follows every new set of recommendations.
  const offer = advice.data;
  const loadAdviceRecommendation = useCallback(
    (signal: AbortSignal) => {
      if (offer === null) return Promise.reject(new Error("Recommendations not loaded"));
      const { as_of_date, horizon_days, buffer_eur } = offer;
      return api.postAdviceRecommendation({ as_of_date, horizon_days, buffer_eur }, signal);
    },
    [offer],
  );
  const adviceRecommendation = useAsyncResource(loadAdviceRecommendation, { enabled: offer !== null });
  const adviceCards = useMemo(
    () => buildAdviceCards(offer?.proposals ?? [], adviceRecommendation.data),
    [offer, adviceRecommendation.data],
  );

  const reloadDashboard = dashboard.reload;
  const reloadAdvice = advice.reload;
  const reloadEvents = events.reload;
  const reloadDecisions = decisionLog.reload;
  const refreshAll = useCallback(() => {
    reloadDashboard();
    reloadAdvice();
    reloadEvents();
    reloadDecisions();
  }, [reloadDashboard, reloadAdvice, reloadEvents, reloadDecisions]);

  // In the single-column layout the assistant sits below everything else: bring the changed forecast into view.
  const mainRef = useRef<HTMLElement>(null);
  const showUpdatedForecast = useCallback(() => {
    refreshAll();
    if (isSingleColumn()) mainRef.current?.scrollIntoView();
  }, [refreshAll]);

  const chat = useChat(params, showUpdatedForecast);
  const assistant = useAssistantPanel();
  // Decisions are taken on the dashboard on screen, and only while it reflects the latest request.
  const boardIsCurrent = !dashboard.loading && dashboard.error === null;
  const decisions = useDecisionFlow(board, boardIsCurrent, recommendation.data?.explanation ?? null, refreshAll);
  const adviceIsCurrent = boardIsCurrent && !advice.loading && advice.error === null;
  const adviceFlow = useAdviceFlow(offer, adviceCards, adviceIsCurrent, refreshAll);
  const planRemoval = usePlanRemoval(refreshAll);
  const recommendedOptionId = board ? pickRecommendedOptionId(board, recommendation.data) : null;
  const chart = useChartPreview(board, recommendedOptionId, adviceCards, adviceFlow.selected);
  const { startNewConversation } = chat;
  const { dismissOutcome } = decisions;
  const { clearSelection, dismissOutcome: dismissAdviceOutcome } = adviceFlow;
  const { reset: resetChartPreview } = chart;

  /** Choices and announcements that refer to the scenario on screen and make no sense for another one. */
  const forgetScenarioChoices = useCallback(() => {
    resetChartPreview();
    clearSelection();
    dismissOutcome();
    dismissAdviceOutcome();
  }, [resetChartPreview, clearSelection, dismissOutcome, dismissAdviceOutcome]);

  const changeParams = useCallback(
    (patch: Partial<ScenarioParams>) => {
      setParams((current) => ({ ...current, ...patch }));
      forgetScenarioChoices();
      setResetDone(false);
      if (patch.as_of_date !== undefined && patch.as_of_date !== params.as_of_date) {
        startNewConversation(`“Today” is now ${formatDate(patch.as_of_date)}. Started a new conversation.`);
      }
    },
    [params.as_of_date, startNewConversation, forgetScenarioChoices],
  );

  async function resetDemo() {
    setResetBusy(true);
    setResetError(null);
    try {
      await api.resetDemo();
      setResetOpen(false);
      forgetScenarioChoices();
      startNewConversation(RESET_OUTCOME.title);
      setResetDone(true);
      refreshAll();
    } catch (error) {
      setResetError(toApiError(error));
    } finally {
      setResetBusy(false);
    }
  }

  const optionsSection =
    board !== null ? (
      <OptionsSection
        dashboard={board}
        recommendation={recommendation.data}
        recommendedOptionId={recommendedOptionId}
        previewOptionId={chart.optionId}
        decisions={decisions}
        onPreview={chart.previewOption}
      />
    ) : null;
  const hasOptions = board !== null && board.options.length > 0;

  // Rescue options come first while there are any; the scenario-bound tabs need a dashboard on screen.
  const tabs: WorkspaceTab<WorkspaceTabId>[] = [];
  if (hasOptions) {
    tabs.push({ id: "options", label: "Options", count: board.options.length, content: optionsSection });
  }
  if (board !== null) {
    tabs.push({
      id: "advice",
      label: "CFO advice",
      count: offer !== null ? adviceCards.length : null,
      content: (
        <AdviceSection
          advice={advice}
          recommendation={adviceRecommendation}
          cards={adviceCards}
          flow={adviceFlow}
          chart={chart}
          baseline={board.forecast.days}
        />
      ),
    });
  }
  tabs.push(
    {
      id: "plans",
      label: "My plans",
      count: events.data?.events.length ?? null,
      content: <PlansList events={events} removal={planRemoval} />,
    },
    {
      id: "history",
      label: "History",
      count: decisionLog.data?.decisions.length ?? null,
      content: <DecisionHistory decisions={decisionLog} />,
    },
  );

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
        assistant={assistant}
        assistantAttention={assistant.open ? null : chatAttention(chat)}
      />

      <div className={assistant.open ? "layout" : "layout layout--assistant-closed"}>
        <main id="main" ref={mainRef} className="layout__main" aria-busy={dashboard.loading}>
          <OutcomeBanner
            outcome={resetDone ? RESET_OUTCOME : null}
            simulated={false}
            onDismiss={() => setResetDone(false)}
          />
          <OutcomeBanner outcome={optionOutcome(decisions.outcome)} simulated onDismiss={decisions.dismissOutcome} />
          <OutcomeBanner outcome={adviceFlow.outcome} simulated onDismiss={adviceFlow.dismissOutcome} />

          {dashboard.error ? (
            <ErrorNotice
              title={
                board === null
                  ? "Your forecast could not be loaded"
                  : "Your forecast could not be updated. The figures below are from the last one that loaded."
              }
              error={dashboard.error}
              onRetry={dashboard.reload}
            />
          ) : null}
          {board === null && dashboard.loading ? <DashboardSkeleton /> : null}
          {board !== null ? (
            <>
              <StatusHero dashboard={board} recommendation={recommendation} />
              <Suspense fallback={<ChartSkeleton />}>
                <BalanceChart dashboard={board} preview={chart.preview} />
              </Suspense>
              {/* With nothing to decide the section is a one-line "all clear" strip, shown without a tab of its own. */}
              {hasOptions ? null : optionsSection}
            </>
          ) : null}

          {/* Not before the first dashboard answer: the tabs would open on "My plans" and then jump to "Options". */}
          {board === null && dashboard.loading ? null : (
            <WorkspaceTabs
              label="Decisions and plans"
              tabs={tabs}
              selectedId={pickActiveTab(tabs, chosenTab)}
              onSelect={setChosenTab}
            />
          )}

          <footer className="trust-line">
            <Icon name="database" size={14} />
            <p>
              Figures: deterministic code on your bank transactions. AI explains and suggests. Nothing happens
              without your approval.
            </p>
          </footer>
        </main>

        <ChatPanel chat={chat} panel={assistant} />
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
