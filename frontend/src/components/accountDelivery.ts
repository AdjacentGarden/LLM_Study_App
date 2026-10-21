type ChallengeDelivery = { delivery: string; demo_code?: string };

const DEMO_EMAILS = new Set([
  "reader@zhiwo.test",
  "xiaolin@zhiwo.test",
  "momo@zhiwo.test",
  "achen@zhiwo.test",
]);

export function acceptedCodeDelivery(
  challenge: ChallengeDelivery,
  email: string,
  demoAvailable: boolean,
): "email" | "demo" | null {
  if (challenge.delivery === "email") return "email";
  if (
    demoAvailable &&
    challenge.delivery === "demo" &&
    DEMO_EMAILS.has(email.trim().toLowerCase()) &&
    /^\d{6}$/.test(challenge.demo_code ?? "")
  ) return "demo";
  return null;
}
