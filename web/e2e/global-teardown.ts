import { cleanupTestData } from "./helpers";

export default function globalTeardown(): void {
  cleanupTestData();
}
