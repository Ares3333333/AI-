"""Local preview: python run.py. No public exposure or paid integrations by default."""
import uvicorn

if __name__=='__main__':
    uvicorn.run('siteapp.web:app',host='127.0.0.1',port=8000,access_log=False,proxy_headers=False)
