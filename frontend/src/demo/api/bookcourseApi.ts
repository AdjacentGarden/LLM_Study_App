import { demoRepository, type DemoRepository } from "../services/DemoRepository";

export type BookCourseRepository = DemoRepository;

/**
 * Compatibility boundary for the migrated screens.
 *
 * The source app calls this object an API client. In the demo it is an
 * in-memory repository backed by committed fixtures, so no browser request
 * can accidentally reach the original backend.
 */
export const bookcourseApi: BookCourseRepository = demoRepository;
