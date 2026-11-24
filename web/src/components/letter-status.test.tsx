import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LetterStatusBadge, letterCountsText } from "./letter-status";

describe("LetterStatusBadge", () => {
  it.each([
    ["new", "Nieuw"],
    ["open", "Openstaand"],
    ["handled", "Afgehandeld"],
  ])("says %s in words", (status, word) => {
    render(<LetterStatusBadge status={status} />);
    expect(screen.getByText(word)).toBeInTheDocument();
    expect(screen.queryByText("Te laat")).not.toBeInTheDocument();
  });

  it("names the day a waiting letter is due back", () => {
    render(<LetterStatusBadge status="waiting" waitingUntil="2026-12-03" />);
    expect(screen.getByText("Wacht op reactie tot 3 dec")).toBeInTheDocument();
  });

  it("says Te laat in words next to the status", () => {
    render(<LetterStatusBadge status="open" overdue />);
    expect(screen.getByText("Openstaand")).toBeInTheDocument();
    expect(screen.getByText("Te laat")).toBeInTheDocument();
  });

  it("shows nothing for a server without a status", () => {
    const { container } = render(<LetterStatusBadge status="" />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("letterCountsText", () => {
  it("lists only the numbers above zero, in order", () => {
    expect(letterCountsText({ new: 2, open: 3, waiting: 2, handled: 14, overdue: 1 })).toBe(
      "2 nieuw, 3 openstaand, 1 te laat, 2 wachten op reactie",
    );
    expect(letterCountsText({ new: 0, open: 0, waiting: 1, handled: 5, overdue: 0 })).toBe(
      "1 wacht op reactie",
    );
    expect(letterCountsText({ new: 0, open: 0, waiting: 0, handled: 5, overdue: 0 })).toBe("");
  });
});
