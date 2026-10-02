# JobPilot Frontend

React + TypeScript + Vite single-page application for the JobPilot local job hunting workflow.

## Technology Stack

- **Framework**: React 18 with TypeScript and Vite
- **Styling**: Tailwind CSS v4 (`@tailwindcss/vite`)
- **Routing**: React Router v6
- **Server State**: TanStack Query v5 (React Query)
- **Testing**: Vitest, React Testing Library, and jsdom
- **Linting & Types**: ESLint 9 (flat config) and TypeScript 5 (`tsc --noEmit`)

## Development Setup

From the `frontend/` directory:

```bash
# Install dependencies
npm install

# Start Vite development server (proxies /api to http://127.0.0.1:8765)
npm run dev
```

In dev mode, API requests use the token configured in `.env.development` (`VITE_JOBPILOT_TOKEN=dev-token-secret-only-for-local-testing`).
When served via `jobpilot ui` in production, the app reads the session token from `<meta name="jobpilot-token">`.

## Available Scripts

- `npm run dev`: Starts the Vite development server with hot module replacement at `http://localhost:5173`.
- `npm run build`: Type-checks and compiles the frontend into `frontend/dist/`. This directory is served by `jobpilot ui`.
- `npm run lint`: Runs ESLint across all source and test files.
- `npm run typecheck`: Runs `tsc --noEmit` to verify TypeScript types without emitting artifacts.
- `npm test`: Runs unit and component test suites via Vitest.
- `npm run test:watch`: Runs Vitest in interactive watch mode.
- `npm run gen:api`: Regenerates the API schema and TypeScript definitions.

## API Schema Regeneration

Frontend API types are strictly derived from the backend FastAPI OpenAPI specifications. Types are never hand-written.

To regenerate OpenAPI definitions and TypeScript types:

```bash
npm run gen:api
```

This command runs:
1. `python3 ../scripts/dump_openapi.py`: Generates `frontend/openapi.json` from the FastAPI application without running a live server.
2. `npx openapi-typescript openapi.json -o src/api/schema.d.ts`: Compiles the OpenAPI schema into strict TypeScript types in `src/api/schema.d.ts`.
