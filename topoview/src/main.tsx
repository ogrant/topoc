import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { loadCluster } from './cluster';
import { App, Message } from './components/App';
import './styles/index.css';

const root = createRoot(document.getElementById('root')!);
void loadCluster().then((loaded) =>
  root.render(
    <StrictMode>
      {'cluster' in loaded ? <App cluster={loaded.cluster} /> : <Message text={loaded.error} />}
    </StrictMode>,
  ),
);
