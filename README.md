A Multi-Agent care assistant that handles the exception-response workflow in an elder-care setting.
When a caregiver reports an anomaly (e.g. "Mr. Zhang's blood pressure jumped to 180/110"), the system:

1. **Triage** — looks up health records, compares against the baseline, and rates severity.
2. **Protocol retrieval** — searches the care knowledge base (RAG) for the matching handling procedure.
3. **Merge & act** — combines both into a recommendation, creates an alert record, and notifies the on-duty nurse.

These tasks need different tools and different system prompts, so they run as separate agents coordinated by a `Coordinator`.