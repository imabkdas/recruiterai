/** Open the PDF fitted to the pane width, without the viewer's thumbnail sidebar. */
export function pdfViewerSrc(url: string): string {
  const [base] = url.split('#');
  return `${base}#navpanes=0&toolbar=0&view=FitH`;
}
