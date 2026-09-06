import { useState } from "react";
import { useBookCourseRepository } from "../context/BookCourseRepositoryContext";
import type { RagQuery, RagResponse } from "../types/api";

export type RagMessage = {
  id: string;
  role: "user" | "assistant";
  text: string;
  response?: RagResponse;
};

export function useRagQuery() {
  const bookcourseRepository = useBookCourseRepository();
  const [response, setResponse] = useState<RagResponse | null>(null);
  const [messages, setMessages] = useState<RagMessage[]>([]);
  const [lastQuery, setLastQuery] = useState<RagQuery | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function ask(payload: RagQuery) {
    if (!payload.question.trim()) {
      const message = "Question is required";
      setError(message);
      throw new Error(message);
    }
    setLastQuery(payload);
    setLoading(true);
    const userMessage = { id: `q_${Date.now()}`, role: "user" as const, text: payload.question };
    setMessages((items) => [...items, userMessage]);
    try {
      const result = await bookcourseRepository.queryRag(payload);
      setResponse(result);
      setMessages((items) => [
        ...items,
        {
          id: `a_${Date.now()}`,
          role: "assistant",
          text: result.answer,
          response: result
        }
      ]);
      setError(null);
      return result;
    } catch (err) {
      const message = err instanceof Error ? err.message : "RAG query failed";
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  }

  async function retry() {
    if (!lastQuery) throw new Error("No RAG query to retry");
    return ask(lastQuery);
  }

  return { ask, retry, response, currentResponse: response, messages, loading, error };
}
