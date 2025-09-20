import os
import argparse
from datetime import datetime, timezone
from github import Github
from github import Auth
from dotenv import load_dotenv


def create_changelog(auth_token, organization, repo_name, file_name):
    auth = Auth.Token(auth_token)

    git = Github(auth=auth)
    try:
        repo = git.get_organization(organization).get_repo(name=repo_name)
    except:
        raise Exception(f"Repository not found for {organization}/{repo_name}")

    releases = repo.get_releases()
    try:
        last_date = datetime.now(timezone.utc)
        previous_date = releases[0].created_at
    except:
        previous_date = repo.created_at
    previous_tag = releases[0].tag_name if releases else None
    current_tag = repo.get_tags()[0].name if repo.get_tags().totalCount > 0 else None
    closed_issues = []
    opened_issues = []
    updated_issues = []
    issues_array = repo.get_issues(state='open', since=previous_date)
    for _, issue in enumerate(issues_array):
        if issue.pull_request:
            continue
        if issue.created_at > last_date:
            continue
        else:
            if issue.created_at > previous_date:
                opened_issues.append(issue)
            else:
                updated_issues.append(issue)

    finished_prs = []
    prs = repo.get_pulls(state='closed', base='main')
    for pr in prs:
        if pr.closed_at < previous_date:
            continue
        finished_prs.append(pr)

    issues_array = repo.get_issues(state='closed')
    sorted_issues = sorted(
        [i for i in issues_array if i.closed_at],
        key=lambda i: i.closed_at,
        reverse=True
    )
    for _, issue in enumerate(sorted_issues):
        if issue.pull_request:
            continue
        if issue.created_at > last_date:
            continue
        if issue.closed_at < previous_date:
            break
        closed_issues.append(issue)

    with open(file_name, "w") as f:
        if previous_tag != None and current_tag != None:
            f.write(f"**Full Changelog**: https://github.com/{organization}/{repo_name}/compare/{previous_tag}...{current_tag}\n\n")
        if (len(finished_prs) > 0):
            f.write(f"# PRs\n\n")
            for pr in finished_prs:
                f.write(f"- [{pr.title}]({pr.html_url})\n")
            f.write("\n\n")
        else:
            f.write("# PRs\n\nNo merged PRs\n\n")
        if (len(closed_issues) + len(opened_issues) + len(updated_issues)) > 0:
            f.write(f"# Issues\n\n")
            if (len(closed_issues) > 0):
                f.write(f"## Closed Issues ({len(closed_issues)})\n")
                for issue in closed_issues:
                    f.write(f"- [{issue.title}]({issue.html_url})\n")
            if (len(opened_issues) > 0):
                f.write(f"\n## Opened Issues ({len(opened_issues)})\n")
                for issue in opened_issues:
                    f.write(f"- [{issue.title}]({issue.html_url})\n")
            if (len(updated_issues) > 0):
                f.write(f"\n## Updated Issues ({len(updated_issues)})\n")
                for issue in updated_issues:
                    f.write(f"- [{issue.title}]({issue.html_url})\n")
        else:
            f.write("# Issues\n\nNo issue changes\n")
    return


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Generate a changelog from GitHub issues.")
    parser.add_argument("organization", type=str, help="Name of the GitHub organization.")
    parser.add_argument("repo_name", type=str, help="Name of the GitHub repository.")
    parser.add_argument("--file_name", type=str, default="body.txt", help="Output file name for the changelog.")
    args = parser.parse_args(argv)
    return args.repo_name, args.organization, args.file_name

def main():
    if os.getenv('GITHUB_ACTIONS') != 'true':
        load_dotenv()
    auth_token = os.getenv('AUTH_TOKEN')
    if not auth_token:
        raise Exception("AUTH_TOKEN environment variable not set.")
    repo_name, organization, file_name = parse_args()
    try:
        create_changelog(auth_token, organization, repo_name, file_name)
    except Exception as e:
        print(f"Error generating changelog: {e}")

if __name__ == "__main__":
    main()
