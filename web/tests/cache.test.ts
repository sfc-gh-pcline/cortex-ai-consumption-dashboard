import { describe, expect, it } from "vitest";

import { TtlCache } from "@/lib/server/cache";

describe("TtlCache", () => {
  it("shares one in-flight load and honors the TTL", async () => {
    let clock = 0;
    const cache = new TtlCache<number>({ maxEntries: 10, now: () => clock });
    let loads = 0;
    const loader = async () => {
      loads += 1;
      return loads;
    };
    const [a, b] = await Promise.all([
      cache.getOrLoad("k", 100, loader),
      cache.getOrLoad("k", 100, loader),
    ]);
    expect(a).toBe(1);
    expect(b).toBe(1);
    clock = 99;
    expect(await cache.getOrLoad("k", 100, loader)).toBe(1);
    clock = 100;
    expect(await cache.getOrLoad("k", 100, loader)).toBe(2);
  });

  it("evicts the least recently used entry beyond the cap", async () => {
    const cache = new TtlCache<string>({ maxEntries: 2, now: () => 0 });
    await cache.getOrLoad("a", 1000, async () => "a");
    await cache.getOrLoad("b", 1000, async () => "b");
    await cache.getOrLoad("a", 1000, async () => "a2"); // refreshes a
    await cache.getOrLoad("c", 1000, async () => "c");
    expect(cache.has("a")).toBe(true);
    expect(cache.has("b")).toBe(false);
    expect(cache.has("c")).toBe(true);
  });

  it("drops rejected loads so the next call retries", async () => {
    const cache = new TtlCache<string>({ maxEntries: 2, now: () => 0 });
    await expect(
      cache.getOrLoad("k", 1000, async () => Promise.reject(new Error("x"))),
    ).rejects.toThrow("x");
    await Promise.resolve();
    expect(cache.has("k")).toBe(false);
    expect(cache.evictWhere((key) => key.startsWith("k"))).toBe(0);
  });

  it("does not let an invalidated in-flight failure evict a newer refresh", async () => {
    const cache = new TtlCache<string>({ maxEntries: 2, now: () => 0 });
    let rejectOld!: (error: Error) => void;
    const old = cache.getOrLoad(
      "k",
      1000,
      () => new Promise<string>((_, reject) => (rejectOld = reject)),
    );
    cache.delete("k");
    expect(await cache.getOrLoad("k", 1000, async () => "fresh")).toBe("fresh");
    rejectOld(new Error("old failure"));
    await expect(old).rejects.toThrow("old failure");
    await Promise.resolve();
    expect(cache.has("k")).toBe(true);
  });
});
