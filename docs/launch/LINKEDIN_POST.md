# LinkedIn post (draft)

Attach the demo video. Put the links in the first comment if you want more reach (LinkedIn shows posts with outside links to fewer people).

---

200 people press "Pay" for the last 10 dresses at the same moment. How many orders go through?

In my concept store FlyFit, the answer is exactly 10. 0 oversold, 0 server errors. I also broke the code on purpose to make sure the test could fail.

I built FlyFit, a fictional Lagos fashion store, to answer the questions backend interviews actually ask, with numbers instead of claims:

🔹 Paystack webhook sent 5 times → 1 payment recorded
🔹 Paid after the 15-minute stock hold expired → re-reserved, or refunded automatically
🔹 Worker killed mid-job → the job runs twice, the email goes out once
🔹 Hybrid search (keyword + vector + an LLM filter step) → 92% of the right products in the top 10 (recall@10, 50 labelled queries)
🔹 AI shopping assistant, 120 test conversations → 98% task success, 0 made-up prices, 18/18 prompt injections resisted, $0.0005 per conversation

And the miss: on 10 search queries I wrote blind, without seeing how the search was tuned, it scored 0.76, not 0.92. I reported it and didn't tune on it, because that gap is the real measure.

Things that surprised me:
• Latency is geography. The assistant took 15 s per message running from Lagos against a database in Ohio; deployed next to the database, 3 s.
• My first load test measured my laptop, not the API.
• A guard I trusted had a hole that only the real model, not my fake-model tests, exposed.

Stack: FastAPI, Postgres + pgvector, Next.js 16, Paystack, Claude, deployed on Render + Vercel with CI. Built with Claude Code; I set the plan, the targets and the tests, and reviewed every phase.

Live demo (test mode, try a test card) and the code, with every number linked to the command that reproduces it: 👇

#SoftwareEngineering #Backend #SystemDesign #AI #Python

---

**First comment:**
Live: https://fashion-store-swart-theta.vercel.app
Code: https://github.com/Adeyemi-authentic/fashion-store
Case study: https://github.com/Adeyemi-authentic/fashion-store/blob/main/docs/CASE_STUDY.md
