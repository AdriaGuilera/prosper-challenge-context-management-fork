# Solution

## First approach: SQL only (not taken)

In the first approach, the agent used SQL queries on the catalog.

This approach had problems:

- The tools did not give feedback. The tools gave only rows of data.
- The agent had to examine the rows and find the meaning.
- This step was slow, and the agent made errors.

## Current approach

### Agent graph

The agent is a graph of six nodes: `greeting`, `answer_questions`, `identify_visit`, `find_slot`, `confirm_booking` and `goodbye`.

Each node gets only the tools that are necessary for its step. Also, the prompt stays small, and the agent cannot do a step too early.

### Tools

The agent has six tools. Each tool is for one step of the call:

- `lookup_patient`
- `search_appointment_types`
- `find_providers`
- `find_locations`
- `check_availability`
- `book_appointment`

Each tool gives these three items to the agent:

- **A status.** The status tells the agent the result. Each tool has its own values (see the table below).
- **An instruction.** The instruction tells the agent the next step. Example: "These are easy to confuse. Ask a question that tells them apart."
- **A maximum of 5 options.** The tool also gives the total number of options. Thus, the context stays small when the catalog is large.

The results contain more text than necessary. This is intentional. The text gives the agent the data that it must have, so the agent does not have to find the answer itself.

### Appointment type: embeddings and the Decisions API

The words of the caller are frequently different from the names in the catalog, since the caller might not know the actual name of the appointment they need. Example: the caller says "my knee hurts". The catalog has the name "Orthopedic Consultation".

The system uses two steps to find the correct type:

1. **Embeddings make a short list.** The system embeds each appointment type name one time, when it loads the catalog. During a call, the system embeds the words of the caller. Then it finds the 10 nearest type names. This step is fast and cheap.
2. **The Decisions API selects the type.** One request goes to the OpenAI Decisions API. The request asks which of the 10 types is correct. The request also has the option "none of these". The API gives a probability for each option.

The system uses the probabilities to set the status:

| Status        | Condition                                                                                     | Example                         | Result                                            |
| ------------- | --------------------------------------------------------------------------------------------- | ------------------------------- | ------------------------------------------------- |
| `match`       | One type has a probability of 0.6 or more.                                                    | "flu shot"                      | The agent confirms the type with the caller.      |
| `ask`         | Two or three types are near. Together, they have 60% or more of the probability of all types. | "an MRI" (brain, spine or knee) | The agent asks the caller to select one type.     |
| `not_offered` | The type is a `match` or an `ask`, but no provider in the clinic offers it.                   | "an eye exam"                   | The agent says that the clinic does not offer it. |
| `no_match`    | "None of these" has the highest probability.                                                  | "Hi, this is Maria"             | The agent asks the reason for the visit.          |
| `unsure`      | All other conditions.                                                                         | —                               | The agent asks the caller for more data.          |

The system keeps all embeddings in a cache table. Thus, the system does not pay for the same text two times (for testing purposes, so that I don't spend twice running the benchmarks).

### Specialties, languages and provider names

The system matches specialties and languages in a different way from provider names. Specialties and languages have a fixed number of values. Provider names do not.

#### Specialties and languages: a list in the tool definition

The system reads these values from the database when the agent starts. It puts them in the tool definitions as a list of allowed values. The model selects one value from the list. Example: the caller says "a heart doctor", and the model writes "Cardiology", not "Cardiologist". In the "given" catalog there are 21 specialties and 7 languages.

The number of values has a limit, also in the real world:

- **Specialties.** The US medical boards recognize about 40 specialties and about 90 subspecialties.
- **Languages.** Languages have a standard list (ISO 639). A clinic group has providers for some tens of languages, not more.

Therefore the list stays small, also when the catalog becomes very large, costing approximately 150 to 200 tokens for each call, making it unnecessary to build a search engine with matching text or embeddings over these 2 fields.

#### Equipment (capabilities): a requirement, not a search

Some visits need equipment. Example: an MRI needs `imaging`, and a blood test needs `lab`. Each location has a list of its equipment. In this demo I decided that callers do not ask for equipment. They ask for a visit. Thus, the model does not get equipment as an argument, and equipment is only a rule in the code:

- The appointment type tells which equipment it needs.
- The tools show only the locations that have this equipment. Example: a caller asks for an MRI near Sunset. Sunset Community Clinic has no imaging, so the agent does not offer it.
- The book appointment tool checks the rule again before it saves the booking.

#### Provider names: a text search

The number of provider names has no limit. A large catalog has thousands of names, so the system cannot put them in the prompt. Also, a name has no meaning, so embeddings do not help. The system needs a text match. The database compares each word of the name with the PostgreSQL extensions `pg_trgm` (spelling similarity) and `fuzzystrmatch` (sound similarity, with `dmetaphone` and `levenshtein`). Thus, "Dr. Garsia" finds "Dr. Maria Garcia", "Dr. Chan" finds "Dr. Chen", and "Dr. Chen" finds all providers with this name.

### Locations

Callers usually say a street or an area name. They do not say a clinic identifier.

A gazetteer (a list of places) changes the place name into a point on the map. Then the system finds the nearest clinics to that point.

Fuzzy matching corrects errors from speech-to-text.

I decided to mock locations, since locations in the given catalog were made up and I can avoid using an API for locations.

### Latency

Latency was one of my priorities for this. Even though I did not focus on voice quality, I wanted to keep latency as small as possible since on a phone call, delays bigger than 2 seconds to answer give a pretty bad customer experience.

To decrease latency:

- Each tool does as much work as possible in one request. Thus, the agent sends fewer requests.
- The tools do the checks. The agent does not do the checks.

## Policies

Since the first moment I knew that policies must be deterministic, and are the sole thing that should always be 100% respected. Since policies are complex, it's hard to represent them in CEL or some language made for checking.

Since code is no longer the bottleneck and it has become cheap and fast to write, I can just make policies into code, and make them highly testable with tests. Since policies live in code, I can just attach policies to the tools that need them.

It made no sense to make policies an LLM node, since I would be checking an output from an LLM with another non-deterministic tool, and if I were to give the ground truth to the LLM, I can just use that ground truth to check with code myself.

The booking rules are Python functions in `backend/store/policies.py`.

## A single service: PostgreSQL

The system uses one PostgreSQL database for all data. The database has these extensions:

- **pgvector:** search with embeddings.
- **PostGIS:** distance between places.
- **pg_trgm:** fuzzy name matching.
- **fuzzystrmatch:** sound-alike name matching.

PostGIS is probably overkill, but thanks to Postgres extensions I was able to keep the "parts" of this demo small, just one database (Postgres) that does it all.

## Mock data

The catalog has no calendar, no patients and no map. I made mock data for the challenge:

- **Locations.** The gazetteer as mentioned above.
- **Calendar.** Each provider gets work hours and some existing bookings. A seed makes the data the same each time.
- **Free slots.** SQL calculates the free slots. The agent does not calculate times.
- **Patients.** A small list of patients, with referrals.
- **More catalogs.** I made 9 more catalogs to test the system: small, large (300 providers), sparse, and catalogs with traps (near-duplicate names, doctors with the same name, difficult referral rules).

## Evaluation

To evaluate the agent, I simulated phone calls in text. Each test is a caller with a goal (for example, "a flu shot near Sunset") and a behavior (vague, impatient, adversarial, and others). gpt-4o plays the caller and talks with the agent until the call ends. The code computes the correct answer from the catalog and the policies: which visits fit, which bookings are valid, and which questions the agent must ask. Then gpt-5.6-terra reads the conversation and judges it against this answer. A conversation passes when the agent reaches a correct result and says nothing wrong. I run the same tests with a baseline agent that gets the full catalog in its prompt, and with 4 different models (since I am testing the harness, not the LLM model itself). The caller model is the same for every agent model, so the comparison is fair.

|                                      | My agent        | Baseline          |
| ------------------------------------ | --------------- | ----------------- |
| Given catalog                        | 82–96%          | 24–67%            |
| 300-provider catalog                 | 75%             | 10–25%            |
| Conversations with a wrong statement | 1%              | 27%               |
| Prompt tokens, given → 300 providers | 22–33k → 27–31k | 80–91k → 379–515k |
| Cost per conversation (gpt-4o)       | $0.051          | $0.111            |

The first two rows are the % of conversations that pass. The ranges are over 4 models: gpt-4o, gpt-4o-mini, gpt-5.6-luna and gpt-5.6-terra.

- My agent is more accurate and cheaper, and its prompt does not grow with the catalog.
- No booking broke a policy, because `book_appointment` checks all the policies in code. A wrong statement is something the agent said, for example a time or a clinic that no tool returned.
- Known gaps: the agent does not ask which MRI the caller needs, and it is weaker when visit names are almost the same.

To reproduce the results:

```bash
make db-up && make ingest CATALOG=all && make eval CATALOG=given MODELS=gpt-4o,gpt-4o-mini,gpt-5.6-luna,gpt-5.6-terra N=2 BASELINE=1
```

## Frontend

The builder is React with Vite, and the graph editor uses `@xyflow/react` (React Flow). I kept it minimal: a list of agents, the graph, a side panel to edit a node, and a test call button. During a test call, you see the call live: the bot sends every node change, tool call and tool result to the browser over the call's own WebRTC connection (Pipecat RTVI messages), and the graph highlights where the agent is.

## Monitoring

Every call is traced with OpenTelemetry and sent to Langfuse. One call is one Langfuse session, with a span for each turn, each STT, LLM and TTS step, and each tool call and edge.

## Things left out & possible improvements

- **Voice quality.** The system uses the voice pipeline as it was supplied. Since the challenge is about context management, that is what I focused on.
- **An ORM (for example, SQLAlchemy).** The system uses SQL directly. SQL is simpler, and you can see each query that the system sends.
- **Multiple clients.** The system serves one clinic group at a time, in order to simplify the challenge. There is no separation of data, access or configuration for each client. The tables already have the catalog name in each key (for testing purposes).
- **Policies for many clients.** The policies are Python functions, written for this catalog's 6 rules. With many clients, each clinic group has its own rules, and a code change for each client does not scale. A next step is to look for a better solution.
- **A real calendar and real patient records.** The calendar, patients and locations are mock data.
- **Audio in the tests.** The tests use text, not audio. The personas copy speech-to-text errors with spelling errors.
- **Large test samples.** Each test ran 1 or 2 times. The results show large differences, but they are not exact rates.
