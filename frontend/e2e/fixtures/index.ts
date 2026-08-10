import { expect, test as base } from "playwright/test";
import { installBookCourseApiFixture, type BookCourseApiFixture } from "./bookcourse-api";

type ResponsiveFixtures = {
  bookCourseApi: BookCourseApiFixture;
};

export const test = base.extend<ResponsiveFixtures>({
  bookCourseApi: async ({ page }, use) => {
    await use(await installBookCourseApiFixture(page));
  }
});

export { expect };
