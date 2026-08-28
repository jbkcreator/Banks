# TECHNICAL PROPOSAL, OVERLAP ANALYSIS & WORK ESTIMATE

## Banks --- Maximum Distribution Build

**Client**\
Josh Kantor

**Lead Developer**\
Hari Krishnan (heu.ai)

**Launch Target**\
Friday, August 28, 2026

**Core Mandate**\
Maximize distribution paths producing interviews, contract-to-hire,
fractional, consulting, or full-time roles. Banks owns the intelligence,
relationship, and multi-channel surround layer after external
application volume (LoopCV/Simplify) is created.

## 1. Master Overlap Netting Table

Foundational code already delivered in Banks Tasks 1--10, Task 2, Task
6, and the Agent Lane baseline has been identified and accounted for to
avoid duplicating existing work.

  -----------------------------------------------------------------------
  Module ID               Functional Module &     Pre-Existing Overlap
                          Scope                   Source
  ----------------------- ----------------------- -----------------------
  MOD-01                  Application Intake,     Reuses Task 6 job
                          Dedupe & Fit Scoring:   parsing utilities and
                          LoopCV/Simplify batch   compensation regex
                          import, manual URL      extractors.
                          parser, deduplication,  
                          Tier A/B/C assignment,  
                          and pursuit mode        
                          selector.               

  MOD-02                  Contact Resolution,     Reuses Banks Task 6
                          Enrichment & Warm       schema structures and
                          Graph: Hiring           company/contact models.
                          manager/recruiter       
                          discovery, email        
                          enrichment fallback to  
                          LinkedIn, and           
                          1st-degree connection   
                          network graph.          

  MOD-03                  7 Distribution Lanes &  Reuses Task 6 draft
                          Surround Workflow: Tier generation templates
                          A surround-the-app      and Resend dispatch
                          generator, 7            helpers.
                          distribution lanes,     
                          proof-of-value briefs,  
                          and safe LinkedIn       
                          clipboard deep-links.   

  MOD-04                  Follow-Up Cadence,      Reuses Task 2 touch
                          Reply-Stop & Ledger:    logging records and
                          Day 3/7/14 scheduler,   rate-capping
                          instant reply-stop      structures.
                          suppression, company    
                          active conversation     
                          freeze, 14-day touch    
                          limits, and funnel      
                          tracking.               

  MOD-05                  Slack Command & Control Reuses Task 2 Slack
                          (Daily Attack Queue):   interactive button
                          Daily Attack Queue      state machine and bot
                          cockpit, interactive    routing.
                          approval buttons, quick 
                          revision hooks, and     
                          company lookup          
                          commands.               

  MOD-06                  Adversarial Exclusion & Net-new end-to-end
                          Launch Staging:         integration and
                          Automated exclusion     exclusion verification.
                          list tests, cross-lane  
                          conflict protection,    
                          mock pipeline runs, and 
                          production deployment   
                          on private repo.        

  TOTAL                   Maximum Distribution    Committed Delivery by
                          Build (Complete Launch  Friday, Aug 28, 2026
                          Scope)                  
  -----------------------------------------------------------------------

## 2. Detailed Module Breakdown

### MOD-01: Application Intake, Deduplication & Fit Scoring

-   LoopCV / Simplify Intake: Daily batch CSV/export parser and
    forwarded email confirmation listener to ingest external
    applications automatically without manual re-entry.
-   Manual Intake Surface: Accepts direct job URLs, pasted job
    descriptions, or quick "I applied here" inputs via Slack and CLI.
-   Deduplication Engine: Normalizes company names and roles across
    disparate application feeds to prevent duplicate opportunity
    records.
-   Fit Scoring & Tiering: Automatically evaluates roles and assigns:
    -   Tier A (Full Surround): Highest-fit opportunities receiving
        immediate multi-channel surround.
    -   Tier B (Moderate Surround): Targeted direct outreach.
    -   Tier C (Application Only / Monitor): Logged for tracking without
        consuming active outreach capacity.
-   Pursuit Mode Classification: Classifies each opportunity into one
    primary pursuit mode: Full-Time Job, Contract-to-Hire, Fractional /
    Retainer, or Paid Consulting / Project.

### MOD-02: Contact Resolution, Enrichment & Warm-Path Graph

-   Hiring Manager & Recruiter Resolution: Resolves functional
    requisition owners (VP Sales, CRO, Head of Growth) and
    internal/external search firm contacts rather than generic HR
    addresses.
-   Verified Contact Enrichment: Multi-source enrichment (Hunter.io /
    Anymail Finder API) resolving verified emails and LinkedIn profile
    URLs. Unverified contacts automatically fall back to LinkedIn.
-   Warm-Path & Referral Graph: Ingests LinkedIn 1st-degree connections
    CSV export and alumni contact lists. Traverses relational paths to
    surface former colleagues, direct mutual connections, and secondary
    referral avenues.
-   Executive Recruiter Registry: Maintains a curated list of GTM,
    PropTech, SaaS, and Fintech executive recruiters for
    direct-relationship outreach.

### MOD-03: Core Distribution Lanes & Surround Workflow

-   Tier A "Surround-the-Application" Engine: Automatically prepares a
    coordinated multi-touch package for top opportunities:
    -   Primary hiring manager email and LinkedIn connection note/DM.
    -   Recruiter / executive search email outreach.
    -   Warm introduction request to mutual connections.
    -   Softer employee/referral networking message.
-   7 Specialized Distribution Lanes:
    -   Hiring Manager Lane: Personalized emails, connection notes, and
        follow-up sequences.
    -   Recruiter Lane: Standing "keep me on file for GTM mandates"
        messaging.
    -   Warm Introduction Lane: Generates requests to mutual connections
        and tracks state (ASKED → AGREED → INTRODUCED → STALLED).
    -   Employee / Referral Lane: Soft networking notes targeting
        internal employees.
    -   Network Activation Lite: Daily proactive list of valuable
        relationships to call, text, email, or message.
    -   No-Open-Role Lite: Strategic executive pitches for companies
        without active postings.
    -   Consulting / Fractional Lite: Contract-to-hire, fractional, and
        project approaches for unposted problem areas.
-   Proof-of-Value Briefs: Generates concise 30/60/90-day points of view
    and GTM observations for top Tier A targets.
-   Human-Safe LinkedIn Handoff: Generates direct profile deep-links and
    copies draft copy to clipboard with zero automated browser
    interaction.

### MOD-04: Follow-up Cadence, Governance & Collision Ledger

-   Automated Cadence: Enforces Day 3, Day 7, and Day 14 follow-up
    sequences across unanswered touches.
-   Immediate Reply-Stop: Instantly suppresses outbound sequences for a
    contact upon receiving a response.
-   Company-Level Collision Protection: Freezes parallel outreach when
    an active conversation starts at a company. Recommends single
    secondary escalation paths rather than mass blasting.
-   Central Governance Ledger: Code-enforces rate caps (20 LinkedIn
    invites/day, 40 emails/day) and 14-day touch spacing per
    contact/channel.
-   Basic Results Tracking: Tracks conversion metrics across the
    pipeline: Applications → Contacts → Replies → Introductions →
    Conversations → Interviews / Paid Work.

### MOD-05: Slack Command & Control / Daily Attack Queue

-   Daily Attack Queue (#banks-jobs): Morning Slack cockpit displaying
    imported applications, Tier A/B surround actions, follow-ups due,
    relationship outreach, and active conversations.
-   Interactive Actions: One-tap Slack buttons to Approve, Skip, or
    Snooze drafts, and mark manual LinkedIn/Call/Text actions complete.
-   Threaded Draft Revisions: Natural language editing directly inside
    Slack threads ("shorter", "less formal", "stronger hook") updating
    draft payloads in real time.
-   On-Demand Retrieval: Quick lookup commands for company status,
    warm-path discovery ("Who do I know at AppFolio"), and daily call
    lists.

### MOD-06: Adversarial Exclusion & Launch Staging

-   Adversarial Exclusion Tests: Automated test suite verifying that
    excluded individuals, companies, or indirect connections through
    excluded firms are blocked at both draft creation and send time.
-   End-to-End Mock Run: Full test run simulating application intake,
    enrichment, scoring, multi-lane draft generation, and Slack approval
    round-trips.
-   Clean Codebase Migration: Migration of the standalone Banks
    repository to your private GitHub organization.

## 3. Explicitly Deferred Features (Out of Launch Scope)

As instructed, the following items are excluded to ensure a fast, robust
launch by August 28:

-   Full public ATS-board scrapers / background polling.
-   Direct browser automation or re-implementation of LoopCV/Simplify
    auto-appliers.
-   Advanced conversational Slack commands beyond the core retrieval
    actions.
-   Autonomous sending / Standing Orders (every touch routes through
    Slack approvals).
-   Complex multi-week dormant network campaign state machines.
-   Automated market signal monitoring (funding rounds, executive
    departures).

## 4. Delivery Confirmation & Launch Phasing

**Delivery Commitment:** The entire scope will be completed, tested, and
deployed to production by Friday, August 28, 2026.

### Milestone Rollout Schedule (Aug 21 -- Aug 28)

  ---------------------------------------------------------------------------------
  Phase             Milestone Target  Modules Delivered Operational Outcome
                    Date                                
  ----------------- ----------------- ----------------- ---------------------------
  Sprint 1: Intake  Mon, Aug 24       MOD-01, MOD-02    Application intake
  & Graph                                               (LoopCV/Simplify/Manual),
  Foundation                                            deduplication, Tier A/B/C
                                                        fit scoring, contact
                                                        resolution, and 1st-degree
                                                        warm-intro graph active.

  Sprint 2:         Wed, Aug 26       MOD-03, MOD-04    All 7 distribution lanes
  Surround &                                            active, Tier A surround
  Multi-Lane Engine                                     pack generator live,
                                                        follow-up
                                                        cadence/reply-stop active,
                                                        and collision ledger
                                                        enforced.

  Sprint 3: Slack   Fri, Aug 28       MOD-05, MOD-06    Daily Attack Queue live in
  Cockpit &                                             #banks-jobs, interactive
  Production Launch                                     approval/revision buttons
                                                        verified, adversarial
                                                        exclusion tests green, and
                                                        system live for daily use.
  ---------------------------------------------------------------------------------
