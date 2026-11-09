// Deploys the Pages artifact under a fresh marker commit, then checks that Pages is really
// serving the new render. Run by actions/github-script from render.yml.
//
// Why not actions/deploy-pages: it always deploys under GITHUB_SHA, and Pages got stuck on a
// repeated SHA for 12 hours while every deploy reported success (2026-11-08). The marker commit
// (main's tree, new message) is created through the Git data API so this job never checks out
// or runs third-party code with a token that can write to the repo.
//
// Env: ARTIFACT_ID, META_SHA256 (sha256 of the meta.bin that was uploaded), DASH_TOKEN.

const crypto = require('crypto')

const TERMINAL_FAILURES = ['deployment_failed', 'deployment_content_failed', 'deployment_attempt_error',
  'deployment_lost', 'deployment_cancelled']
const sleep = ms => new Promise(r => setTimeout(r, ms))

module.exports = async ({ github, context, core }) => {
  const { owner, repo } = context.repo
  const base = context.sha

  // 1. Marker commit on the deploy-marker branch.
  const { data: baseCommit } = await github.request('GET /repos/{owner}/{repo}/git/commits/{sha}', { owner, repo, sha: base })
  const { data: marker } = await github.request('POST /repos/{owner}/{repo}/git/commits', {
    owner, repo,
    message: `render ${context.runId}`,
    tree: baseCommit.tree.sha,
    parents: [base],
    author: { name: 'render', email: 'render@users.noreply.github.com', date: new Date().toISOString() },
  })
  try {
    await github.request('PATCH /repos/{owner}/{repo}/git/refs/{ref}', { owner, repo, ref: 'heads/deploy-marker', sha: marker.sha, force: true })
  } catch (e) {
    if (e.status !== 422 && e.status !== 404) throw e
    await github.request('POST /repos/{owner}/{repo}/git/refs', { owner, repo, ref: 'refs/heads/deploy-marker', sha: marker.sha })
  }
  core.info(`marker commit ${marker.sha}`)

  // 2. Pages deployment under the marker SHA.
  const oidc_token = await core.getIDToken()
  const { data: dep } = await github.request('POST /repos/{owner}/{repo}/pages/deployments', {
    owner, repo,
    artifact_id: Number(process.env.ARTIFACT_ID),
    pages_build_version: marker.sha,
    oidc_token,
  })
  core.info(`deployment ${dep.id} created`)

  // 3. Wait for it, tolerating transient API errors; cancel it if we give up.
  let status = 'unknown'
  let errors = 0
  const deadline = Date.now() + 5 * 60 * 1000
  try {
    while (Date.now() < deadline) {
      await sleep(5000)
      try {
        const { data: s } = await github.request('GET /repos/{owner}/{repo}/pages/deployments/{id}', { owner, repo, id: dep.id })
        status = s.status
        errors = 0
      } catch (e) {
        if (++errors >= 10) throw e
        core.warning(`status check failed (${errors}/10): ${e.message}`)
        continue
      }
      core.info(`status: ${status}`)
      if (status === 'succeed') break
      if (TERMINAL_FAILURES.includes(status)) throw new Error(`Pages deployment ended with status ${status}`)
    }
    if (status !== 'succeed') throw new Error(`Pages deployment still "${status}" after 5 minutes`)
  } catch (e) {
    if (status !== 'succeed') {
      await github.request('POST /repos/{owner}/{repo}/pages/deployments/{id}/cancel', { owner, repo, id: dep.id }).catch(() => {})
    }
    throw e
  }

  // 4. Confirm the live site serves what we built. This is the check that was missing when
  //    Pages silently kept an old render: a green deploy that changes nothing fails here.
  const url = `https://${owner}.github.io/${repo}/${process.env.DASH_TOKEN}/meta.bin`
  const want = process.env.META_SHA256
  const verifyDeadline = Date.now() + 3 * 60 * 1000
  let got = ''
  while (Date.now() < verifyDeadline) {
    try {
      const res = await fetch(`${url}?v=${context.runId}-${Date.now()}`, { headers: { 'Cache-Control': 'no-cache' } })
      if (res.ok) {
        got = crypto.createHash('sha256').update(Buffer.from(await res.arrayBuffer())).digest('hex')
        if (got === want) {
          core.info('live site serves the new render')
          return
        }
      } else {
        core.info(`live fetch: HTTP ${res.status}`)
      }
    } catch (e) {
      core.info(`live fetch failed: ${e.message}`)
    }
    await sleep(10000)
  }
  core.setFailed(`Pages reported success but still serves an old render (got ${got.slice(0, 12)}…, want ${want.slice(0, 12)}…)`)
}
