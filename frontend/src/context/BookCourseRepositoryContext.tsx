import { createContext, useContext, type ReactNode } from "react";
import type { BookCourseRepository } from "../api/bookcourseApi";

const BookCourseRepositoryContext = createContext<BookCourseRepository | null>(null);

export function BookCourseRepositoryProvider({
  children,
  repository
}: {
  children: ReactNode;
  repository: BookCourseRepository;
}) {
  return (
    <BookCourseRepositoryContext.Provider value={repository}>
      {children}
    </BookCourseRepositoryContext.Provider>
  );
}

export function useBookCourseRepository() {
  const repository = useContext(BookCourseRepositoryContext);
  if (!repository) {
    throw new Error("useBookCourseRepository must be used within a BookCourseRepositoryProvider");
  }
  return repository;
}
