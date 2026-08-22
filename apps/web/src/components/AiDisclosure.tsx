/**
 * Disclosure that artificial intelligence took part in producing what is on screen.
 *
 * Utah's Artificial Intelligence Policy Act requires AI use to be disclosed where the
 * person affected by it encounters it -- not buried in a policy document. So the banner
 * form sits in the app shell on every screen, and the detailed form appears on the case
 * screen, next to the verdicts it is about.
 *
 * The detailed wording is specific on purpose. "This system uses AI" would be true and
 * useless; a reviewer deciding how much weight to give a verdict needs to know *which*
 * steps a model performed and which ones it did not, because the two carry very
 * different kinds of risk. The split it describes is the project's central design claim
 * (ADR-0003), and the console is where that claim is either honoured or quietly dropped.
 */
export function DisclosureBanner() {
  return (
    <div className="disclosure">
      <strong>AI disclosure.</strong> Cases in this console are prepared with the help of
      an artificial-intelligence system. It never issues a denial; every adverse
      determination is made by a licensed clinician.
    </div>
  );
}

export function DisclosureDetail() {
  return (
    <section className="disclosure" aria-labelledby="ai-disclosure-heading">
      <strong id="ai-disclosure-heading">
        How artificial intelligence was used on this case
      </strong>
      <ul>
        <li>
          A language model read the retrieved coverage policy and proposed the criteria
          below, including the wording of each one and the passage it came from.
        </li>
        <li>
          A language model judged the criteria marked <span className="tag">judgment</span>,
          by reading the member&rsquo;s clinical notes. Each such verdict is shown with the
          quoted passages it rests on, and a quote that does not appear verbatim in the
          record is discarded before you see it.
        </li>
        <li>
          Criteria marked <span className="tag">threshold</span>,{" "}
          <span className="tag">enum</span> and <span className="tag">temporal</span> were
          decided by database queries and arithmetic, not by a model.
        </li>
        <li>
          The system can approve a request or refer it to you. It has no ability to deny
          one. Any adverse determination on this case is yours.
        </li>
      </ul>
    </section>
  );
}
