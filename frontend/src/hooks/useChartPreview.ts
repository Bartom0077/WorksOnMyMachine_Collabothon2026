import { useCallback, useMemo, useState } from "react";
import { type AdviceCard, advicePreview, changesForecast } from "../lib/advice";
import { type ChartPreview, optionPreview } from "../lib/chartModel";
import type { Dashboard } from "../types/api";

export interface ChartPreviewState {
  /** The scenario drawn on the chart next to the baseline forecast. */
  preview: ChartPreview | null;
  /** The rescue option on the chart, if it shows one. */
  optionId: string | null;
  /** The single recommendation the owner asked to see on the chart, if any. */
  proposalId: string | null;
  /** Every recommendation that is part of the scenario on the chart. */
  shownProposalIds: string[];
  /** Draws a rescue option; null returns to the recommended one. */
  previewOption: (optionId: string | null) => void;
  /** Draws one recommendation alone; asking for the one already drawn returns to the selection. */
  previewProposal: (proposalId: string) => void;
  /** Draws the selected recommendations together. */
  previewSelection: () => void;
  reset: () => void;
}

/**
 * Decides what the chart previews. A rescue option and CFO recommendations are never drawn together: the
 * owner's most recent explicit choice wins. Without a choice the recommended rescue option is drawn.
 */
export function useChartPreview(
  board: Dashboard | null,
  recommendedOptionId: string | null,
  cards: AdviceCard[],
  selected: AdviceCard[],
): ChartPreviewState {
  const [chosenOptionId, setChosenOptionId] = useState<string | null>(null);
  const [chosenProposalId, setChosenProposalId] = useState<string | null>(null);
  const [adviceChosenLast, setAdviceChosenLast] = useState(false);

  const option = board?.options.find((candidate) => candidate.id === (chosenOptionId ?? recommendedOptionId)) ?? null;
  const baseline = board?.forecast.days ?? null;
  const advice = useMemo(() => {
    if (!adviceChosenLast || baseline === null) return null;
    const chosen = cards.find((card) => card.proposal.id === chosenProposalId);
    const drawn = (chosen !== undefined ? [chosen] : selected).filter((card) => changesForecast(card.proposal));
    const preview = advicePreview(baseline, drawn);
    if (preview === null) return null;
    return { preview, proposalId: chosen?.proposal.id ?? null, shownIds: drawn.map((card) => card.proposal.id) };
  }, [adviceChosenLast, baseline, cards, chosenProposalId, selected]);
  const optionScenario = useMemo(() => (option === null ? null : optionPreview(option)), [option]);

  const previewOption = useCallback((optionId: string | null) => {
    setChosenOptionId(optionId);
    setAdviceChosenLast(false);
  }, []);

  const previewProposal = useCallback((proposalId: string) => {
    setChosenProposalId((current) => (current === proposalId ? null : proposalId));
    setAdviceChosenLast(true);
  }, []);

  const previewSelection = useCallback(() => {
    setChosenProposalId(null);
    setAdviceChosenLast(true);
  }, []);

  const reset = useCallback(() => {
    setChosenOptionId(null);
    setChosenProposalId(null);
    setAdviceChosenLast(false);
  }, []);

  return {
    preview: advice?.preview ?? optionScenario,
    optionId: advice === null ? (option?.id ?? null) : null,
    proposalId: advice?.proposalId ?? null,
    shownProposalIds: advice?.shownIds ?? [],
    previewOption,
    previewProposal,
    previewSelection,
    reset,
  };
}
