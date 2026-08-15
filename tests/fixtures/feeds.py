"""Feed documents used by the ingestion tests.

Held as literals rather than fetched, so the suite is deterministic and offline. Between
them these cover what real feeds actually throw at a parser: RSS 2.0 and Atom, tracking
parameters, a duplicate item repeated under a new GUID, missing fields, an unparseable
document, and the same wire story carried by two different publishers.
"""

RSS_BASIC = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example Wire</title>
    <link>https://wire.example</link>
    <description>Wire copy</description>
    <item>
      <title>Power outage hits 50,000 customers</title>
      <link>https://wire.example/outage?id=1&amp;utm_source=rss&amp;utm_medium=feed</link>
      <guid isPermaLink="false">wire-0001</guid>
      <description>&lt;p&gt;A regional outage began early Tuesday.&lt;/p&gt;</description>
      <pubDate>Tue, 11 Aug 2026 08:12:00 GMT</pubDate>
      <author>reporter@wire.example (A Reporter)</author>
      <enclosure url="https://wire.example/img/outage.jpg" type="image/jpeg" length="1024"/>
    </item>
    <item>
      <title>BREAKING: Utility crews dispatched</title>
      <link>https://wire.example/crews?id=2</link>
      <guid isPermaLink="false">wire-0002</guid>
      <description>Crews were dispatched within the hour.</description>
      <pubDate>Tue, 11 Aug 2026 08:41:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""

#: The same two items, plus one new one. Item 1 reappears with a *different* GUID and a
#: different tracking parameter — the case §13's example is about.
RSS_SECOND_FETCH = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example Wire</title>
    <link>https://wire.example</link>
    <item>
      <title>Power outage hits 50,000 customers</title>
      <link>https://wire.example/outage?id=1&amp;utm_campaign=repost</link>
      <guid isPermaLink="false">wire-0001-repost</guid>
      <description>&lt;p&gt;A regional outage began early Tuesday.&lt;/p&gt;</description>
      <pubDate>Tue, 11 Aug 2026 08:12:00 GMT</pubDate>
    </item>
    <item>
      <title>BREAKING: Utility crews dispatched</title>
      <link>https://wire.example/crews?id=2</link>
      <guid isPermaLink="false">wire-0002</guid>
      <description>Crews were dispatched within the hour.</description>
      <pubDate>Tue, 11 Aug 2026 08:41:00 GMT</pubDate>
    </item>
    <item>
      <title>Officials give estimated restoration time</title>
      <link>https://wire.example/restore?id=3</link>
      <guid isPermaLink="false">wire-0003</guid>
      <description>Power is expected back by evening.</description>
      <pubDate>Tue, 11 Aug 2026 09:18:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""

ATOM_BASIC = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example Times</title>
  <link href="https://times.example"/>
  <subtitle>A newspaper</subtitle>
  <updated>2026-08-11T09:00:00Z</updated>
  <entry>
    <title>Storm knocks out power across the region</title>
    <link rel="alternate" href="https://www.times.example/storm-power/"/>
    <id>tag:times.example,2026:storm-power</id>
    <published>2026-08-11T08:27:00Z</published>
    <updated>2026-08-11T08:30:00Z</updated>
    <author><name>T Writer</name></author>
    <summary type="html">&lt;p&gt;Tens of thousands lost power.&lt;/p&gt;</summary>
  </entry>
</feed>
"""

#: A feed with items that cannot be attributed — no link, or no title. Both must be
#: dropped rather than stored: Non-Negotiable #2 requires a working link back.
RSS_UNUSABLE_ITEMS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Sloppy Feed</title>
    <item>
      <title>Has a title but no link</title>
      <guid isPermaLink="false">sloppy-1</guid>
    </item>
    <item>
      <link>https://sloppy.example/no-title</link>
      <guid isPermaLink="false">sloppy-2</guid>
    </item>
    <item>
      <title>This one is fine</title>
      <link>https://sloppy.example/fine</link>
      <guid isPermaLink="false">sloppy-3</guid>
      <pubDate>Tue, 11 Aug 2026 10:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""

#: Malformed but not hopeless — an unescaped ampersand. feedparser flags it and still
#: returns the entry; we should keep the article and record the complaint.
RSS_MALFORMED_BUT_USABLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Bad XML & Co</title>
    <item>
      <title>Rates rise & markets react</title>
      <link>https://bad.example/rates</link>
      <guid isPermaLink="false">bad-1</guid>
      <pubDate>Tue, 11 Aug 2026 11:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""

NOT_A_FEED = "<html><body><h1>404 Not Found</h1></body></html>"
