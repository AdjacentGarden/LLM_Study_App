import { describe, expect, it } from "vitest";
import { assignmentExercises, getNextAssignmentExerciseIndex } from "./assignmentExercises";

describe("assignmentExercises", () => {
  it("keeps the required automatic exercise order", () => {
    expect(assignmentExercises.map((exercise) => exercise.id)).toEqual([
      "judgment",
      "choice",
      "short-answer"
    ]);
  });

  it("advances through the sequence and stops on the short answer", () => {
    expect(getNextAssignmentExerciseIndex(0)).toBe(1);
    expect(getNextAssignmentExerciseIndex(1)).toBe(2);
    expect(getNextAssignmentExerciseIndex(2)).toBe(2);
  });
});

