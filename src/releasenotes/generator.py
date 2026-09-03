import argparse
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

from anthropic import Anthropic
from dotenv import load_dotenv
from github import Auth
from github import Github
from github.GithubException import GithubException


def _latest_release_or_none(repo):
    try:
        return repo.get_latest_release()
    except GithubException as error:
        if error.status == 404:
            return None
        raise


def _get_repo(client: Github, organization: str, repo_name: str):
    try:
        return client.get_organization(organization).get_repo(name=repo_name)
    except GithubException as error:
        if error.status == 404:
            raise Exception(f"Repository not found for {organization}/{repo_name}") from error
        if error.status == 401:
            raise Exception("AUTH_TOKEN is invalid") from error
        if error.status == 403:
            raise Exception("AUTH_TOKEN does not have enough permission or rate limit was exceeded") from error
        raise


def _tag_timestamp(tag) -> datetime:
    author_date = getattr(getattr(getattr(tag, "commit", None), "commit", None), "author", None)
    if author_date and author_date.date:
        return author_date.date
    committer_date = getattr(getattr(getattr(tag, "commit", None), "commit", None), "committer", None)
    if committer_date and committer_date.date:
        return committer_date.date
    raise Exception(f"Unable to resolve timestamp for tag '{tag.name}'")


def _resolve_change_window(repo, release) -> tuple[datetime, datetime, str | None, str | None]:
    tags = list(repo.get_tags())
    tags.sort(key=_tag_timestamp, reverse=True)

    if not tags:
        previous_date = release.created_at if release else repo.created_at
        previous_tag = release.tag_name if release else None
        return previous_date, datetime.now(timezone.utc), previous_tag, None

    requested_tag = os.getenv("GITHUB_REF_NAME")
    current_index = 0
    if requested_tag:
        for index, tag in enumerate(tags):
            if tag.name == requested_tag:
                current_index = index
                break

    current_tag = tags[current_index]
    previous_tag = tags[current_index + 1] if current_index + 1 < len(tags) else None

    previous_date = _tag_timestamp(previous_tag) if previous_tag else (release.created_at if release else repo.created_at)
    current_date = _tag_timestamp(current_tag)
    return previous_date, current_date, (previous_tag.name if previous_tag else None), current_tag.name


def _collect_commit_messages(repo, previous_tag: str | None, current_tag: str | None) -> list[str]:
    if not previous_tag or not current_tag:
        return []

    comparison = repo.compare(previous_tag, current_tag)
    commit_messages: list[str] = []
    for commit in comparison.commits:
        first_line = commit.commit.message.splitlines()[0].strip()
        if first_line:
            commit_messages.append(first_line)
    return commit_messages


def _summarize_changes_with_claude(
    *,
    organization: str,
    repo_name: str,
    previous_tag: str | None,
    current_tag: str | None,
    prs: list,
    commits: list[str],
    closed_issues: list,
    opened_issues: list,
    updated_issues: list,
    ai_model: str,
    ai_max_items: int,
) -> str | None:
    api_key = os.getenv("AI_API_KEY")
    if not api_key:
        print("AI summary requested, but AI_API_KEY is not set. Skipping AI summary.")
        return None

    max_items = max(10, ai_max_items)
    pr_items = [pr.title for pr in prs][:max_items]
    closed_items = [issue.title for issue in closed_issues][:max_items]
    opened_items = [issue.title for issue in opened_issues][:max_items]
    updated_items = [issue.title for issue in updated_issues][:max_items]
    commit_items = commits[:max_items]

    payload_context = {
        "repo": f"{organization}/{repo_name}",
        "compare": f"{previous_tag}...{current_tag}" if previous_tag and current_tag else None,
        "counts": {
            "prs": len(prs),
            "commits": len(commits),
            "closed_issues": len(closed_issues),
            "opened_issues": len(opened_issues),
            "updated_issues": len(updated_issues),
        },
        "prs": pr_items,
        "commits": commit_items,
        "closed_issues": closed_items,
        "opened_issues": opened_items,
        "updated_issues": updated_items,
    }

    system_prompt = (
        "You are a release note assistant. "
        "Use only the provided data. Do not invent features, fixes, or issue states. "
        "Return concise markdown bullets only."
    )
    user_prompt = (
        "Create a short release summary with these sections:"
        "\n### Highlights"
        "\n### Fixes"
        "\n### Technical Changes"
        "\nUse at most 8 total bullets."
        "\nIf a section has no relevant items, omit it."
        "\nInput data:\n"
        f"{json.dumps(payload_context, ensure_ascii=False)}"
    )

    try:
        client = Anthropic(api_key=api_key)

        response = client.messages.create(
            model=ai_model,
            max_tokens=500,
            system=system_prompt,
            messages=[
                {"role": "user", "content": user_prompt},
            ],
        )
    except Exception as error:
        print(f"AI summary failed: {str(error)[:500]}. Continuing without AI summary.")
        return None

    if not response.content:
        print("AI summary failed (empty model response). Continuing without AI summary.")
        return None

    content = response.content[0].text.strip()
    if not content:
        print("AI summary failed (blank model response). Continuing without AI summary.")
        return None
    return content


def _send_discord_changelog(
    *,
    webhook_url: str,
    role_tags: list[str],
    organization: str,
    repo_name: str,
    version: str,
    changelog_path: str,
) -> None:
    webhook_url = webhook_url.strip().strip('"').strip("'")

    with open(changelog_path, "r", encoding="utf-8") as file_handle:
        changelog = file_handle.read().strip()

    if version and version != "latest":
        release_page = f"https://github.com/{organization}/{repo_name}/releases/tag/{version}"
    else:
        release_page = f"https://github.com/{organization}/{repo_name}/releases"

    title0 = f"# 🎉 **New release for {repo_name}!**".strip()
    title1 = f"📦 Grab it here: [{version}]({release_page})".strip()
    header_lines = [title0, title1]
    header = "\n".join(header_lines)
    role_footer = " ".join(role_tags).strip()

    # Discord's hard cap is 2000 chars. Every chunk gets wrapped in a header
    # and/or a pagination/role footer, so the split budget must leave room
    # for whichever wrapping ends up largest, not just the raw chunk text.
    # `header` is always >= the continuation-chunk prefix (title0 alone), so
    # sizing against it covers every chunk, not just the first.
    discord_limit = 2000
    pagination_buffer = len("\n\n(cont. 9999/9999)")
    role_footer_overhead = (2 + len(role_footer)) if role_footer else 0
    max_overhead = len(header) + 2 + pagination_buffer + role_footer_overhead
    max_len = max(200, discord_limit - max_overhead)
    chunks: list[str] = []
    remaining = changelog
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, max_len)
        if split_at <= 0:
            split_at = max_len
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip("\n")

    if not chunks:
        chunks = ["No changelog content generated."]

    total = len(chunks)
    for index, chunk in enumerate(chunks, start=1):
        if total == 1:
            content = f"{header}\n\n{chunk}".strip()
        elif index == 1:
            content = f"{header}\n\n{chunk}\n\n(1/{total})".strip()
        else:
            content = f"{title0} (cont. {index}/{total})\n\n{chunk}".strip()

        if role_footer and index == total:
            content = f"{content}\n\n{role_footer}".strip()

        if len(content) > discord_limit:
            content = content[: discord_limit - 3].rstrip() + "..."

        payload = json.dumps({"content": content}).encode("utf-8")
        request = urllib.request.Request(
            webhook_url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "AutoChangelog-WebhookClient/1.0 (+https://github.com/XPGAMESLLC/AutoChangelog)",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                status = response.getcode()
                if status < 200 or status >= 300:
                    raise RuntimeError(f"Discord webhook returned status {status}")
        except urllib.error.HTTPError as error:
            response_body = ""
            try:
                response_body = error.read().decode("utf-8", errors="replace").strip()
            except Exception:
                response_body = ""

            hint = ""
            if error.code == 403:
                hint = (
                    "403 Forbidden from Discord webhook. Common causes: webhook was regenerated/revoked, "
                    "webhook belongs to a channel where posting is restricted, or the URL token is no longer valid."
                )
                if "1010" in response_body:
                    hint += (
                        " Discord/Cloudflare returned code 1010 (client blocked). "
                        "Try sending via PowerShell Invoke-RestMethod from the same machine/network to verify if this "
                        "is a local network/client signature block."
                    )
            elif error.code == 404:
                hint = "404 from Discord webhook. The webhook URL is usually invalid or deleted."

            details = f"Discord webhook HTTP {error.code}: {error.reason}"
            if response_body:
                details += f" | response: {response_body[:500]}"
            if hint:
                details += f" | hint: {hint}"
            raise RuntimeError(details) from error

    print(f"Sent {total} Discord message(s).")


def _parse_role_tags(single_tag: str, multi_tags: str) -> list[str]:
    values = [single_tag or ""]
    if multi_tags:
        normalized = multi_tags.replace("\n", ",").replace(";", ",")
        values.extend(normalized.split(","))

    parsed: list[str] = []
    for value in values:
        role = value.strip()
        if role and role not in parsed:
            parsed.append(role)
    return parsed


def create_changelog(
    auth_token: str,
    organization: str,
    repo_name: str,
    file_name: str,
    ai_summary: bool = False,
    ai_model: str = "claude-haiku-4-5-20251001",
    ai_max_items: int = 120,
) -> None:
    client = Github(auth=Auth.Token(auth_token))
    repo = _get_repo(client, organization, repo_name)

    release = _latest_release_or_none(repo)
    previous_date, current_date, previous_tag, current_tag = _resolve_change_window(repo, release)
    commit_messages = _collect_commit_messages(repo, previous_tag, current_tag)

    opened_issues = []
    updated_issues = []
    closed_issues = []
    finished_prs = []

    open_issues = repo.get_issues(state="open", since=previous_date)
    for issue in open_issues:
        if issue.raw_data.get("pull_request"):
            continue
        if issue.created_at > current_date:
            continue
        if issue.created_at > previous_date:
            opened_issues.append(issue)
        elif issue.updated_at and previous_date < issue.updated_at <= current_date:
            updated_issues.append(issue)

    closed_prs = repo.get_pulls(state="closed", base="main")
    for pr in closed_prs:
        if pr.closed_at and previous_date < pr.closed_at <= current_date:
            finished_prs.append(pr)

    issues_array = repo.get_issues(state="closed", since=previous_date)
    sorted_issues = sorted(
        [issue for issue in issues_array if issue.closed_at],
        key=lambda issue: issue.closed_at,
        reverse=True,
    )
    for issue in sorted_issues:
        if issue.raw_data.get("pull_request"):
            continue
        if issue.created_at > current_date:
            continue
        if issue.closed_at <= previous_date:
            break
        if issue.closed_at <= current_date:
            closed_issues.append(issue)

    ai_summary_markdown: str | None = None
    if ai_summary:
        ai_summary_markdown = _summarize_changes_with_claude(
            organization=organization,
            repo_name=repo_name,
            previous_tag=previous_tag,
            current_tag=current_tag,
            prs=finished_prs,
            commits=commit_messages,
            closed_issues=closed_issues,
            opened_issues=opened_issues,
            updated_issues=updated_issues,
            ai_model=ai_model,
            ai_max_items=ai_max_items,
        )

    with open(file_name, "w", encoding="utf-8") as file_handle:
        if previous_tag is not None and current_tag is not None:
            file_handle.write(
                f"**Full Changelog**: https://github.com/{organization}/{repo_name}/compare/{previous_tag}...{current_tag}\n\n"
            )

        if ai_summary_markdown:
            file_handle.write("## AI Summary\n\n")
            file_handle.write(f"{ai_summary_markdown}\n\n")

        if finished_prs:
            file_handle.write("# PRs\n\n")
            for pr in finished_prs:
                file_handle.write(f"- [{pr.title}]({pr.html_url})\n")
            file_handle.write("\n\n")
        else:
            file_handle.write("# PRs\n\nNo merged PRs\n\n")

        if closed_issues or opened_issues or updated_issues:
            file_handle.write("# Issues\n\n")

            if closed_issues:
                file_handle.write(f"## Closed Issues ({len(closed_issues)})\n")
                for issue in closed_issues:
                    file_handle.write(f"- [{issue.title}]({issue.html_url})\n")

            if opened_issues:
                file_handle.write(f"\n## Opened Issues ({len(opened_issues)})\n")
                for issue in opened_issues:
                    file_handle.write(f"- [{issue.title}]({issue.html_url})\n")

            if updated_issues:
                file_handle.write(f"\n## Updated Issues ({len(updated_issues)})\n")
                for issue in updated_issues:
                    file_handle.write(f"- [{issue.title}]({issue.html_url})\n")
        else:
            file_handle.write("# Issues\n\nNo issue changes\n")


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Generate a changelog from GitHub issues.")
    parser.add_argument("organization", type=str, help="Name of the GitHub organization.")
    parser.add_argument("repo_name", type=str, help="Name of the GitHub repository.")
    parser.add_argument("--file_name", type=str, default="body.txt", help="Output file name for the changelog.")
    parser.add_argument("--ai-summary", action="store_true", help="Enable Claude AI summary section.")
    parser.add_argument("--ai-model", type=str, default=os.getenv("AI_MODEL", "claude-haiku-4-5-20251001"), help="Claude model name.")
    parser.add_argument("--ai-max-items", type=int, default=120, help="Max items per category sent to AI.")
    parser.add_argument(
        "--discord-webhook-url",
        type=str,
        default=os.getenv("DISCORD_WEBHOOK_URL", ""),
        help="Discord webhook URL used to send changelog notifications.",
    )
    parser.add_argument(
        "--discord-role-tag",
        type=str,
        default=os.getenv("DISCORD_ROLE_TAG", ""),
        help="Optional Discord role mention, e.g. <@&1234567890>",
    )
    parser.add_argument(
        "--discord-role-tags",
        type=str,
        default=os.getenv("DISCORD_ROLE_TAGS", ""),
        help="Optional list of role mentions separated by commas/newlines/semicolons.",
    )
    return parser.parse_args(argv)


def main() -> None:
    if os.getenv("GITHUB_ACTIONS") != "true":
        load_dotenv()

    auth_token = os.getenv("AUTH_TOKEN")
    if not auth_token:
        raise Exception("AUTH_TOKEN environment variable not set.")

    args = parse_args()
    try:
        create_changelog(
            auth_token,
            args.organization,
            args.repo_name,
            args.file_name,
            ai_summary=args.ai_summary,
            ai_model=args.ai_model,
            ai_max_items=args.ai_max_items,
        )

        normalized_webhook_url = args.discord_webhook_url.strip().strip('"').strip("'")
        if normalized_webhook_url:
            release_version = os.getenv("GITHUB_REF_NAME") or "latest"
            role_tags = _parse_role_tags(args.discord_role_tag, args.discord_role_tags)
            _send_discord_changelog(
            webhook_url=normalized_webhook_url,
                role_tags=role_tags,
                organization=args.organization,
                repo_name=args.repo_name,
                version=release_version,
                changelog_path=args.file_name,
            )
    except Exception as error:
        print(f"Error generating changelog: {error}")
        raise


if __name__ == "__main__":
    main()
