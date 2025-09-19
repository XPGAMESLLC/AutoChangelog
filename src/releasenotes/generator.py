import os
import argparse
from datetime import datetime, timezone
from github import Github
from github import Auth
from dotenv import load_dotenv


def create_changelog(auth_token, repo_name):
    auth = Auth.Token(auth_token)

    git = Github(auth=auth)
    try:
        repo = git.get_organization("XPGAMESLLC").get_repo(name=repo_name)
    except:
        return

    releases = repo.get_releases()
    try:
        last_date = datetime.now(timezone.utc)
        previous_date = releases[0].created_at
    except:
        previous_date = repo.created_at
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
    totalIssues = len(list(sorted_issues))
    for index, issue in enumerate(sorted_issues):
        if issue.pull_request:
            continue
        if issue.created_at > last_date:
            continue
        if issue.closed_at < previous_date:
            break
        closed_issues.append(issue)

    # create markdown file with lists of closed opened and updated issues, up them in a list of links to issues
    file_name = "release_body.txt"
    with open(file_name, "w") as f:
        if (len(finished_prs) > 0):
            f.write(f"# PRs\n\n")
            for pr in finished_prs:
                f.write(f"- [{pr.title}]({pr.html_url})\n")
            f.write("\n\n")
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
    return file_name


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Generate a changelog from GitHub issues.")
    parser.add_argument("repo_name", type=str, help="Name of the GitHub repository.")
    args = parser.parse_args(argv)
    return args.repo_name

def main():
    if os.getenv('GITHUB_ACTIONS') != 'true':
        load_dotenv()
    auth_token = os.getenv('AUTH_TOKEN')
    repo_name = parse_args()
    file_name = create_changelog(auth_token, repo_name)

if __name__ == "__main__":
    main()