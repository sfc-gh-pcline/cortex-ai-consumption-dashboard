// Process-wide TTL + LRU cache. Replaces st.cache_data: one instance per Node
// process, which is exactly one Application Service instance (min/max 1).
// Values are stored as promises so concurrent callers share one in-flight load.

export interface CacheOptions {
  readonly maxEntries: number;
  readonly now?: () => number;
}

interface Entry<V> {
  value: Promise<V>;
  expiresAt: number;
  generation: number;
}

export class TtlCache<V> {
  private readonly entries = new Map<string, Entry<V>>();
  private readonly maxEntries: number;
  private readonly now: () => number;
  private readonly generations = new Map<string, number>();

  constructor(options: CacheOptions) {
    this.maxEntries = options.maxEntries;
    this.now = options.now ?? Date.now;
  }

  // Returns the cached value or runs `loader`, caching its promise for `ttlMs`.
  // A rejected load is evicted so the next caller retries.
  async getOrLoad(key: string, ttlMs: number, loader: () => Promise<V>): Promise<V> {
    const existing = this.entries.get(key);
    if (existing && existing.expiresAt > this.now()) {
      this.entries.delete(key);
      this.entries.set(key, existing); // refresh LRU position
      return existing.value;
    }
    const value = loader();
    const generation = this.generations.get(key) ?? 0;
    this.entries.set(key, { value, expiresAt: this.now() + ttlMs, generation });
    value.catch(() => {
      if (this.entries.get(key)?.generation === generation) this.entries.delete(key);
    });
    while (this.entries.size > this.maxEntries) {
      const oldest = this.entries.keys().next().value;
      if (oldest === undefined) break;
      this.entries.delete(oldest);
    }
    return value;
  }

  has(key: string): boolean {
    const entry = this.entries.get(key);
    return entry !== undefined && entry.expiresAt > this.now();
  }

  delete(key: string): void {
    this.generations.set(key, (this.generations.get(key) ?? 0) + 1);
    this.entries.delete(key);
  }

  // Evicts every key for which `predicate` is true.
  evictWhere(predicate: (key: string) => boolean): number {
    let count = 0;
    for (const key of [...this.entries.keys()]) {
      if (predicate(key)) {
        this.entries.delete(key);
        count += 1;
      }
    }
    return count;
  }

  clear(): void {
    for (const key of this.entries.keys()) {
      this.generations.set(key, (this.generations.get(key) ?? 0) + 1);
    }
    this.entries.clear();
  }

  get size(): number {
    return this.entries.size;
  }
}

export const MINUTES = 60_000;
export const HOURS = 60 * MINUTES;
