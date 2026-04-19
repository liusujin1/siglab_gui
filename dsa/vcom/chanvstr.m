function sout = chanvstr(Action,In1,In2)
% function sout = chanvstr(Action,In1,In2)
% Returns vector of strings for channel full scale selector,
% In1 = max range, 
% In2 = index if Action = 'volts'
% or the real number for channel full scale.

NRANGE = 10; % number of voltage ranges 
  if strcmp(Action,'list') || strcmp(Action,'list_auto')
    stmp='';
    for i=1:NRANGE,
      v = In1*(2^(1-i));      % related by 2 for SigLabs
      s=[volt2str(v,'lo')];
      stmp=put_str(i,stmp,s);
    end % main loop
    sout=stmp;
  elseif strcmp(Action,'volts')
    if In2<=NRANGE
       sout = min(In1,In1*(2^(1-In2)));    % number returned for full scale voltage
    else
       sout = -1;  % indicate auto_range selection with negative number
    end
  end

%   if strcmp(Action,'list_auto')
%      sout=put_str(NRANGE+1,sout,'Auto');  % append auto_range selection to end of list
%   end








