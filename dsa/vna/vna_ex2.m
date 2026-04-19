% script vna_ex2
% Search for all transfer functions in a file and plot them .... 


load sample.vna -mat       % get a vna data file

figure                     % make a figure 
axes('color',[0 0 0])      % an axes
icolor      = 1;
numcolor    = 32;   
some_colors = hsv(numcolor);     % get some colors 

for refc = 1:4
    for respc = 1:16
        if ~isempty(SLm.xcmeas(refc,respc).xfer)
           line('xdata',SLm.fdxvec,'ydata',20*log10(SLm.xcmeas(refc,respc).xfer),...
                'color',some_colors(icolor,:));
           icolor=max(1,mod(icolor+1,numcolor));
        end;
    end;
end;
title(['Transfer Functions']);
xlabel('Hertz');
ylabel('dB');

