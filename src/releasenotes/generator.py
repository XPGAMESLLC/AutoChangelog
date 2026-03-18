import argparse
import json
import os
from datetime import datetime, timezone
from urllib import request
from urllib.error import HTTPError, URLError

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


def _summarize_changes_with_chatgpt(
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

    base_url = os.getenv("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    chat_endpoint = f"{base_url}/chat/completions"
    responses_endpoint = f"{base_url}/responses"

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

    chat_body = {
        "model": ai_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 500,
    }

    responses_body = {
        "model": ai_model,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "max_output_tokens": 500,
    }

    request_headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        chat_request = request.Request(
            chat_endpoint,
            data=json.dumps(chat_body).encode("utf-8"),
            headers=request_headers,
            method="POST",
        )
        with request.urlopen(chat_request, timeout=45) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        error_body = ""
        try:
            error_body = error.read().decode("utf-8", errors="ignore")
        except Exception:
            error_body = ""

        if error.code == 400:
            try:
                responses_request = request.Request(
                    responses_endpoint,
                    data=json.dumps(responses_body).encode("utf-8"),
                    headers=request_headers,
                    method="POST",
                )
                with request.urlopen(responses_request, timeout=45) as response:
                    responses_payload = json.loads(response.read().decode("utf-8"))

                content = responses_payload.get("output_text", "").strip()
                if content:
                    return content

                output_items = responses_payload.get("output", [])
                for item in output_items:
                    if item.get("type") != "message":
                        continue
                    for content_item in item.get("content", []):
                        if content_item.get("type") in {"output_text", "text"}:
                            text_value = content_item.get("text", "").strip()
                            if text_value:
                                return text_value

                print("AI summary failed (Responses API returned no text). Continuing without AI summary.")
                return None
            except HTTPError as fallback_error:
                fallback_body = ""
                try:
                    fallback_body = fallback_error.read().decode("utf-8", errors="ignore")
                except Exception:
                    fallback_body = ""
                print(
                    f"AI summary failed (HTTP 400, fallback HTTP {fallback_error.code}). "
                    f"Chat error: {error_body[:400]} Fallback error: {fallback_body[:400]}"
                )
                return None
            except URLError:
                print("AI summary failed (Responses API network error). Continuing without AI summary.")
                return None

        print(f"AI summary failed (HTTP {error.code}): {error_body[:500]}")
        return None
    except URLError:
        print("AI summary failed (network error). Continuing without AI summary.")
        return None

    choices = response_payload.get("choices", [])
    if not choices:
        print("AI summary failed (empty model response). Continuing without AI summary.")
        return None

    content = choices[0].get("message", {}).get("content", "").strip()
    if not content:
        print("AI summary failed (blank model response). Continuing without AI summary.")
        return None
    return content


def create_changelog(
    auth_token: str,
    organization: str,
    repo_name: str,
    file_name: str,
    ai_summary: bool = False,
    ai_model: str = "gpt-4o-mini",
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
        if issue.pull_request:
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

    issues_array = repo.get_issues(state="closed")
    sorted_issues = sorted(
        [issue for issue in issues_array if issue.closed_at],
        key=lambda issue: issue.closed_at,
        reverse=True,
    )
    for issue in sorted_issues:
        if issue.pull_request:
            continue
        if issue.created_at > current_date:
            continue
        if issue.closed_at <= previous_date:
            break
        if issue.closed_at <= current_date:
            closed_issues.append(issue)

    ai_summary_markdown: str | None = None
    if ai_summary:
        ai_summary_markdown = _summarize_changes_with_chatgpt(
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
    parser.add_argument("--ai-summary", action="store_true", help="Enable ChatGPT summary section.")
    parser.add_argument("--ai-model", type=str, default=os.getenv("AI_MODEL", "gpt-4o-mini"), help="ChatGPT model name.")
    parser.add_argument("--ai-max-items", type=int, default=120, help="Max items per category sent to AI.")
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
    except Exception as error:
        print(f"Error generating changelog: {error}")
        raise


if __name__ == "__main__":
    main()
