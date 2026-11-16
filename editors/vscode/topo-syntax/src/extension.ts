// Starts the .topo language server (`topoc lsp`) for .topo files: diagnostics
// as you type, an outline, go to definition, references, hover, semantic
// colouring and completion. The grammars in this extension highlight without
// it.

import * as vscode from "vscode";
import {
  LanguageClient,
  LanguageClientOptions,
  ServerOptions,
} from "vscode-languageclient/node";

let client: LanguageClient | undefined;

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration(async (event) => {
      if (event.affectsConfiguration("topo")) {
        await stop();
        await start();
      }
    }),
  );
  await start();
}

export async function deactivate(): Promise<void> {
  await stop();
}

async function start(): Promise<void> {
  const config = vscode.workspace.getConfiguration("topo");
  const command = config.get<string[]>("server.command") ?? [];
  if (command.length === 0) {
    return; // the server is turned off
  }
  const server: ServerOptions = { command: command[0], args: command.slice(1) };
  const options: LanguageClientOptions = {
    documentSelector: [{ language: "topo" }],
    initializationOptions: { contextFiles: config.get<object>("contextFiles") ?? {} },
  };
  client = new LanguageClient("topo", "Topo language server", server, options);
  try {
    await client.start();
  } catch (error) {
    client = undefined;
    void vscode.window.showErrorMessage(
      `Topo: cannot start '${command.join(" ")}' (set topo.server.command): ${error}`,
    );
  }
}

async function stop(): Promise<void> {
  const running = client;
  client = undefined;
  await running?.stop();
}
