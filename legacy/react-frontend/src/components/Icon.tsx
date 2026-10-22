export type IconName = "home" | "book" | "spark" | "user" | "arrow" | "back" | "cards" | "check" | "send" | "clock" | "community" | "upload" | "edit" | "mic";
const paths: Record<IconName, string> = {
  edit: "m15 4 5 5M4 20l5-1L20 8a2 2 0 0 0-5-5L4 14zM4 20h16",
  mic: "M9 5a3 3 0 0 1 6 0v7a3 3 0 0 1-6 0zM5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8",
  upload: "M12 16V4m-5 5 5-5 5 5M5 14v5a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-5",
  community: "M16 21v-2a5 5 0 0 0-10 0v2M15 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0ZM18 4a3 3 0 0 1 0 6m1 4a4 4 0 0 1 3 4v2",
  home: "m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z",
  book: "M12 5v16M3 4h5a4 4 0 0 1 4 2 4 4 0 0 1 4-2h5v15h-5a5 5 0 0 0-4 2 5 5 0 0 0-4-2H3z",
  spark: "m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5zM20 2v4m-2-2h4",
  user: "M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0ZM4 21v-2a8 8 0 0 1 16 0v2",
  arrow: "M4 12h16m-6-6 6 6-6 6", back: "M20 12H4m6-6-6 6 6 6",
  cards: "M7 7h13v14H7zM4 17H2V2h13v2", check: "m5 12 4 4L19 6",
  send: "m3 3 18 9-18 9 4-9zm4 9h14", clock: "M12 7v5l3 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z",
};
export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]}/></svg>;
}
