import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LabelChip } from "./label-chip";

const base = { id: "x", document_count: 0, system_key: "" };

describe("LabelChip", () => {
  it("shows the name with the class of its colour", () => {
    render(<LabelChip label={{ ...base, name: "Zorg", color: "red" }} />);
    const chip = screen.getByText("Zorg");
    expect(chip).toHaveClass("label-chip", "label-red");
  });

  it("shows an unknown colour as grey", () => {
    render(<LabelChip label={{ ...base, name: "Nieuw", color: "magenta" }} />);
    expect(screen.getByText("Nieuw")).toHaveClass("label-chip", "label-grey");
    expect(screen.getByText("Nieuw")).not.toHaveClass("label-magenta");
  });
});
