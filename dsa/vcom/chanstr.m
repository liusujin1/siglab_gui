function s=chanstr(numin,numout,sin)
% function s=chanstr(numin,numout,sin) 
% Returns strings for channel select popup controls
% Dick Benson DSP Technology
% sin = suffix input 
   if nargin <= 2   s = sprintf('Ch%d|',1:numin);  end; 
   if nargin == 2   s = [s, sprintf('Out%d|',1:numout)];  end; 
   if nargin == 3   s = sprintf(['Ch%d' sin '|'],1:numin);  end; 
   s = s(1:length(s)-1);   % remove last vertical bar
% end function








