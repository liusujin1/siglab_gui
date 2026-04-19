function vna_quick_relayout(layout)
%VNA_QUICK_RELAYOUT One-click layout refresh for VNA plot window.
%   vna_quick_relayout           -> refresh current display (default: double)
%   vna_quick_relayout('double') -> force dual-axis layout
%   vna_quick_relayout('single') -> force single-axis layout

if nargin < 1 || isempty(layout)
    layout = 'double';
end

fig = findobj('type','figure','tag','vna_plot');
if isempty(fig)
    error('VNA plot window is not open.');
end
figure(fig(1));

switch lower(layout)
    case 'single'
        plot_vna('single');
    case 'double'
        plot_vna('double');
    otherwise
        error('layout must be ''single'' or ''double''.');
end

drawnow;
end
