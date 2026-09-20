import React from "react";
import { createRoot } from "react-dom/client";
import { StudioProvider, StudioEntry } from "../../src/components/LearningStudio";
import "../../src/styles/learning-studio.css";

createRoot(document.getElementById("root")!).render(
  <StudioProvider><StudioEntry anchor={{ book_id: "test-book", chapter_title: "测试章节" }} /></StudioProvider>,
);
