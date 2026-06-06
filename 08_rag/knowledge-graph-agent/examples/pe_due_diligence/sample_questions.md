# Sample due-diligence questions

These questions are the acceptance test for the PE flagship example.
A correctly working hybrid agent should answer all five with citations
back to the source documents.

1. **What are the top three integration risks for Acme Corp's recent
   acquisition of FinFlow?**
   _Hybrid graph traversal: Acme → OWNS → FinFlow → contracts /
   litigation / talent retention concerns._
2. **Which executives at FinFlow have prior fintech experience, and
   where did they work before?**
   _Vector-only RAG would surface the bios, but the agent should
   traverse PERSON → WORKS_AT to enumerate prior employers cleanly._
3. **Is there any path between Maria Chen and Sequoia Capital, and what
   is it?**
   _Multi-hop: Maria Chen WORKS_AT FinFlow ←─ OWNS ─ Sequoia, plus the
   historical "principal at Sequoia 2011-2018" relationship._
4. **Are there any contractual landmines that the FinFlow acquisition
   activates?**
   _Should surface the MegaBank change-of-control clause with the
   FirstUnion competitive overlap._
5. **Which executives have left Acme since the FinFlow announcement,
   and where did they go?**
   _Should pull Linda Okafor → DataPress Co (which the news file
   notes is also Sequoia-backed)._
