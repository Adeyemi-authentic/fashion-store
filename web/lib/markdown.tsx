import Link from "next/link";
import type { ReactNode } from "react";

// The small markdown subset the policy pages and the assistant's replies use: headings,
// paragraphs, "- " lists, simple tables, [links](/path) and **bold**. Built as React
// elements (never raw HTML), so text from the API can't inject markup. External links were
// already removed by the API's link check; any that remain open in a new tab.

type Options = { onLinkClick?: () => void };

function inline(text: string, opts: Options, key = ""): ReactNode[] {
  const out: ReactNode[] = [];
  const pattern = /\[([^\]]+)\]\(([^)\s]+)\)|\*\*([^*]+)\*\*/g;
  let last = 0;
  for (const m of text.matchAll(pattern)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const k = `${key}-${m.index}`;
    if (m[1] !== undefined) {
      const href = m[2];
      out.push(
        href.startsWith("/") && !href.startsWith("//") ? (
          <Link key={k} href={href} onClick={opts.onLinkClick} className="font-medium underline underline-offset-2">
            {m[1]}
          </Link>
        ) : (
          <a key={k} href={href} target="_blank" rel="noopener noreferrer nofollow" className="underline">
            {m[1]}
          </a>
        ),
      );
    } else {
      out.push(<strong key={k}>{m[3]}</strong>);
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function cells(row: string): string[] {
  return row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
}

export function Markdown({ text, onLinkClick, compact = false }: { text: string; onLinkClick?: () => void; compact?: boolean }) {
  const opts = { onLinkClick };
  const blocks = text.replace(/\r/g, "").split(/\n{2,}/);
  const gap = compact ? "mt-2" : "mt-4";
  return (
    <>
      {blocks.map((block, i) => {
        const lines = block.split("\n").filter((l) => l.trim() !== "");
        if (lines.length === 0) return null;
        const first = lines[0];
        const cls = i === 0 ? "" : gap;
        if (first.startsWith("# ")) {
          return <h1 key={i} className={`font-display text-3xl ${cls}`}>{inline(first.slice(2), opts, `${i}`)}</h1>;
        }
        if (first.startsWith("## ")) {
          return (
            <section key={i}>
              <h2 className={`font-display text-xl ${compact ? "mt-3" : "mt-8"}`}>{inline(first.slice(3), opts, `${i}`)}</h2>
              {lines.length > 1 && <Markdown text={lines.slice(1).join("\n")} onLinkClick={onLinkClick} compact={compact} />}
            </section>
          );
        }
        if (lines.length >= 2 && lines.every((l) => l.trim().startsWith("|")) && /^[\s|:-]+$/.test(lines[1])) {
          const [head, , ...rows] = lines;
          return (
            <div key={i} className={`overflow-x-auto ${cls}`}>
              <table className="w-full min-w-80 text-left text-sm">
                <thead className="border-b border-line">
                  <tr>{cells(head).map((c, j) => <th key={j} className="py-2 pr-4 font-medium">{c}</th>)}</tr>
                </thead>
                <tbody>
                  {rows.map((r, j) => (
                    <tr key={j} className="border-b border-line/60">
                      {cells(r).map((c, k) => <td key={k} className="py-2 pr-4">{inline(c, opts, `${i}-${j}-${k}`)}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        }
        // Paragraph lines and "- " list lines, possibly mixed in one block ("Some options:\n- A\n- B").
        const runs: { list: boolean; lines: string[] }[] = [];
        for (const l of lines) {
          const list = /^\s*[-*] /.test(l);
          const run = runs.at(-1);
          if (run && run.list === list) run.lines.push(list ? l.replace(/^\s*[-*] /, "") : l);
          else runs.push({ list, lines: [list ? l.replace(/^\s*[-*] /, "") : l] });
        }
        return (
          <div key={i} className={cls}>
            {runs.map((run, r) =>
              run.list ? (
                <ul key={r} className={`list-disc space-y-1 pl-5 ${r > 0 ? "mt-1" : ""}`}>
                  {run.lines.map((l, j) => <li key={j}>{inline(l, opts, `${i}-${r}-${j}`)}</li>)}
                </ul>
              ) : (
                <p key={r} className={r > 0 ? "mt-1" : ""}>
                  {run.lines.map((l, j) => (
                    <span key={j}>
                      {j > 0 && <br />}
                      {inline(l, opts, `${i}-${r}-${j}`)}
                    </span>
                  ))}
                </p>
              ),
            )}
          </div>
        );
      })}
    </>
  );
}
