# Turning on accounts, leaderboard and fan scorecards

The site works without this (guest mode). These steps switch on Google/Discord
sign-in, server-side picks, the leaderboard and the community scorecard.
About 30 minutes, one time. Do them in order.

## 1. Create the database (Supabase, free)
1. Sign up at **supabase.com** → **New project**. Name it `pickafight`, pick a
   strong database password (save it in your password manager), region **West US**.
2. When it's ready: left menu **SQL Editor** → **New query** → paste the whole of
   [`supabase/setup.sql`](supabase/setup.sql) → **Run**. It should say "Success".

## 2. Allow your site to receive sign-ins
**Authentication → URL Configuration**
- **Site URL:** `https://pickafight.now`
- **Redirect URLs:** add `https://pickafight.now/` and `https://pickafight.now/index.html`

## 3. Google sign-in
1. In Supabase: **Authentication → Sign In / Providers → Google** — copy the
   **Callback URL** (looks like `https://xxxx.supabase.co/auth/v1/callback`).
2. Go to **console.cloud.google.com** → create a project (`Pick A Fight`).
3. Open **Google Auth Platform**:
   - **Branding:** app name `Pick A Fight`, your support email.
   - **Audience:** External.
   - **Data Access:** add the `openid` scope (email and profile are already there).
   - **Clients → Create client → Web application**
     - Authorized JavaScript origins: `https://pickafight.now`
     - Authorized redirect URIs: the Supabase **Callback URL** from step 1
4. Copy the **Client ID** and **Client Secret** → back in Supabase's Google
   provider page, paste both, switch it **on**, **Save**.

## 4. Discord sign-in
1. **discord.com/developers** → **New Application** → name `Pick A Fight`.
2. Left menu **OAuth2** → **Redirects → Add Redirect** → paste the same Supabase
   **Callback URL** → **Save Changes**.
3. Copy **Client ID** and **Client Secret** → Supabase **Authentication →
   Providers → Discord** → paste, switch **on**, **Save**.

## 5. Let GitHub sync fights and settle picks
1. Supabase: **Project Settings → API Keys** → copy the **secret** key
   (`sb_secret_…`). This one is private — it only goes into GitHub.
2. GitHub repo → **Settings → Secrets and variables → Actions → New repository
   secret** → Name `SUPABASE_SECRET_KEY`, value = the secret key → **Add secret**.

## 6. Send Claude two things (both are public by design)
- **Project URL** (`https://xxxx.supabase.co`, under Project Settings → Data API)
- **Publishable key** (`sb_publishable_…`, under Project Settings → API Keys)

Never send or paste the **secret** key anywhere except the GitHub secret in
step 5. The site build refuses to run if a secret key ends up in
`site_config.json`.

---
After step 6, Claude adds the URL + publishable key to `site_config.json`, the
site rebuilds with a **Sign in** button, and the next daily run syncs fights
into the database.
