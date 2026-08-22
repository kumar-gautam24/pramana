import { redirect } from "next/navigation";

/** There is no landing page. A reviewer opening this console wants the queue. */
export default function Home() {
  redirect("/cases");
}
