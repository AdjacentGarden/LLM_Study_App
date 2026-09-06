import { expect, test } from "playwright/test";

test.describe("real community catalog UI", () => {
  test("shows verifiable books, source rights, and never reports a fake demo import", async ({ page }) => {
    const calculus = {
      id: "gutenberg_calculus_made_easy",
      title: "Calculus Made Easy",
      catalog_title: "Calculus Made Easy",
      author: "Silvanus P. Thompson",
      cover: "/api/community/books/gutenberg_calculus_made_easy/cover",
      subject: "数学",
      level: "大学",
      language: "English",
      edition: "Second Edition, 1914",
      page_count: 292,
      file_size_bytes: 1298365,
      source_page_url: "https://www.gutenberg.org/ebooks/33283",
      license_name: "Project Gutenberg License / U.S. public-domain text",
      license_url: "https://www.gutenberg.org/policy/license",
      rights_notice: "Project Gutenberg 标注该作品在美国不受版权限制。",
      description: "服务器保存并导入完整 292 页 PDF。",
      chapters: ["Differentiation", "Integration"],
      tags: ["真实 PDF", "微积分", "公共领域"],
      server_cached: true,
      imported_book_id: null
    };
    const catalog = [
      calculus,
      { ...calculus, id: "gutenberg_euclid_elements", title: "The Elements of Euclid", catalog_title: "Euclid's Elements", author: "Euclid", page_count: 228, subject: "数学" },
      { ...calculus, id: "gutenberg_quaternions_physics", title: "Utility of Quaternions in Physics", catalog_title: "Quaternions in Physics", author: "Alexander McAulay", page_count: 134, subject: "物理" }
    ];
    let importRequestCount = 0;
    await page.route("**/api/community/books/gutenberg_calculus_made_easy/import", async (route) => {
      importRequestCount += 1;
      await route.fulfill({ json: {
        catalog_id: calculus.id,
        book_id: "book_real_calculus",
        filename: "calculus-made-easy.pdf",
        size_bytes: calculus.file_size_bytes,
        status: "ready",
        already_imported: false
      }});
    });
    await page.route("**/api/community/books/gutenberg_calculus_made_easy", (route) => route.fulfill({ json: calculus }));
    await page.route("**/api/community/books", (route) => route.fulfill({ json: catalog }));

    await page.goto("/?embedded=device-preview");
    await page.locator(".primary-nav .nav-item").nth(1).click();

    const cards = page.locator(".community-book-card");
    await expect(cards).toHaveCount(3);
    await expect(cards.first()).toContainText("292 页 · 服务器已缓存");
    await cards.first().click();

    await expect(page.locator(".community-detail-screen")).toBeVisible();
    await expect(page.locator(".community-detail-stats")).toContainText("292 页");
    await page.getByRole("tab", { name: "来源与版权" }).click();
    await expect(page.getByRole("link", { name: /Project Gutenberg 原始书目/ })).toHaveAttribute(
      "href",
      "https://www.gutenberg.org/ebooks/33283"
    );

    const geometry = await page.evaluate(() => ({
      documentWidth: document.documentElement.scrollWidth,
      viewportWidth: document.documentElement.clientWidth
    }));
    expect(geometry.documentWidth).toBeLessThanOrEqual(geometry.viewportWidth);

    await page.getByRole("button", { name: "从服务器加入课程" }).click();
    await expect.poll(() => importRequestCount).toBe(1);
    await expect(page.getByRole("heading", { name: "calculus-made-easy" })).toBeVisible();
    await expect(page.getByRole("button", { name: "开始解析" })).toBeVisible();
    await expect(page.getByText("设备预览模式不执行网络下载")).toHaveCount(0);
  });
});
