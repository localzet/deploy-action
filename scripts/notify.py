"""Optional, best-effort notification; never reveal bot tokens or change deployment status."""
import json
import os
import re
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def payload(env):
    token = env.get('DEPLOY_TELEGRAM_BOT_TOKEN', '')
    chat = env.get('DEPLOY_TELEGRAM_CHAT_ID', '')
    if not token and not chat:
        return None
    if not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+', token) or not re.fullmatch(r'-?[0-9]+|@[A-Za-z0-9_]+', chat):
        raise ValueError('Invalid Telegram credentials')
    repo = env.get('GITHUB_REPOSITORY', 'unknown repository')
    run = env.get('GITHUB_RUN_ID', '')
    server = env.get('GITHUB_SERVER_URL', 'https://github.com')
    operation = env.get('DEPLOY_OPERATION', 'deploy')
    outcome = env.get('DEPLOY_OUTCOME', 'unknown')
    message = f'{repo}\nOperation: {operation}\nResult: {outcome}'
    if env.get('DEPLOY_RELEASE'):
        message += '\nRelease: '+env['DEPLOY_RELEASE']
    if run:
        message += f'\n{server}/{repo}/actions/runs/{run}'
    return token, {'chat_id': chat, 'text': message[:4000], 'disable_web_page_preview': True}


def main():
    try:
        args = payload(os.environ)
        if args is None:
            return
        token, body = args
        request = urllib.request.Request(
            'https://api.telegram.org/bot'+token+'/sendMessage',
            data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'},
            method='POST')
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=8) as response:
            if not json.load(response).get('ok'):
                raise RuntimeError('Telegram rejected notification')
        print('Telegram notification sent')
    except Exception:
        print('::warning::Telegram notification failed (deployment result unchanged)')


if __name__ == '__main__':
    main()
